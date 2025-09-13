#!/usr/bin/env python3
"""
CrazeDyn Web Panel - Modern Web-Based Server Management
Fast, responsive, and accessible from anywhere
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from flask_socketio import SocketIO, emit, join_room
from functools import wraps
import secrets
import hashlib
import threading
import time
import os
import sys
from pathlib import Path
import json
import psutil
import socket

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))
try:
    from app.core.server_manager import ServerManager
    from app.core.downloader import PaperMCDownloader
    from app.core.server_store import get_server_store
    print("✅ Core modules imported successfully")
except ImportError as e:
    print(f"⚠️ Could not import core modules: {e}")
    # Create dummy classes for development
    from app.core.server_store import get_server_store
    
    class ServerManager:
        def __init__(self):
            self.server_store = get_server_store()
            self.servers = {}
            self.load_servers()
        def load_servers(self): 
            # Use unified server store
            try:
                data = self.server_store.load_servers()
                for name, server_data in data.items():
                    self.servers[name] = type('Server', (), server_data)()
            except Exception as e:
                print(f"Error loading servers in web panel: {e}")
        def get_console_output(self, name): return "Console not available - server manager not loaded"
        def send_command(self, name, command): return False
        def start_server(self, name): return False
        def stop_server(self, name): return False
        def restart_server(self, name): return False
        def get_server_status(self, name): return "unknown"
    
    class PaperMCDownloader:
        def __init__(self): pass

app = Flask(__name__)
# Use stable secret key from environment, generate if not set
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_COOKIE_SECURE'] = os.getenv('HTTPS_ENABLED', 'False').lower() == 'true'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Initialize SocketIO with dynamic CORS for LAN access while maintaining security
def get_allowed_origins():
    """Get allowed origins including localhost and current host IP"""
    origins = ["http://localhost:5000", "http://127.0.0.1:5000"]
    try:
        import socket
        hostname = socket.gethostname()
        host_ip = socket.gethostbyname(hostname)
        origins.extend([f"http://{host_ip}:5000", f"http://{hostname}:5000"])
    except:
        pass
    return origins

socketio = SocketIO(app, cors_allowed_origins=get_allowed_origins(), async_mode='threading')

# Secure SocketIO connections
@socketio.on('connect')
def on_connect():
    if 'authenticated' not in session or not session['authenticated']:
        return False  # Reject connection

# Authentication system with email/password setup
import bcrypt
import json
from pathlib import Path

def get_admin_config_path():
    """Get path to admin config file"""
    data_dir = os.getenv('CRAZEDYN_DATA_DIR', str(Path(__file__).parent.parent))
    return Path(data_dir) / 'admin_config.json'

def save_admin_credentials(email, password):
    """Save admin credentials securely"""
    try:
        # Hash password with bcrypt
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
        
        config = {
            'email': email,
            'password_hash': hashed_password.decode('utf-8'),
            'created_at': time.time()
        }
        
        config_path = get_admin_config_path()
        with open(config_path, 'w') as f:
            json.dump(config, f)
        
        # Secure the file permissions (owner read/write only)
        config_path.chmod(0o600)
        return True
    except Exception as e:
        print(f"Error saving admin credentials: {e}")
        return False

def load_admin_credentials():
    """Load admin credentials"""
    try:
        config_path = get_admin_config_path()
        if not config_path.exists():
            return None
        
        with open(config_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading admin credentials: {e}")
        return None

def verify_admin_credentials(email, password):
    """Verify admin email and password"""
    config = load_admin_credentials()
    if not config:
        return False
    
    if config['email'] != email:
        return False
    
    try:
        return bcrypt.checkpw(password.encode('utf-8'), config['password_hash'].encode('utf-8'))
    except Exception:
        return False

# Check if admin setup is complete
admin_config = load_admin_credentials()
if admin_config:
    print(f"🔐 Authentication enabled - Admin: {admin_config['email'][:3]}***@{admin_config['email'].split('@')[1] if '@' in admin_config['email'] else 'hidden'}")
else:
    print("⚡ First-time setup required - Admin credentials not configured")

def login_required(f):
    """Decorator to require login for protected routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'authenticated' not in session or not session['authenticated']:
            if request.is_json:
                return jsonify({'success': False, 'message': 'Authentication required'})
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def validate_server_name(name):
    """Validate server name to prevent injection attacks"""
    import re
    if not re.match(r'^[A-Za-z0-9_-]+$', name):
        return False
    return name in server_manager.servers

# Authentication routes
@app.route('/setup', methods=['GET', 'POST'])
def setup():
    """Initial admin setup page"""
    # Check if setup is already complete
    if load_admin_credentials():
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        # Validation
        if not email or '@' not in email:
            flash('Please enter a valid email address.', 'error')
        elif len(password) < 8:
            flash('Password must be at least 8 characters long.', 'error')
        elif password != confirm_password:
            flash('Passwords do not match.', 'error')
        else:
            # Save credentials and login
            if save_admin_credentials(email, password):
                session['authenticated'] = True
                session['admin_email'] = email
                session.permanent = True
                flash('🎉 Welcome to CrazeDynPanel v2.0! Setup complete.', 'success')
                return redirect(url_for('dashboard'))
            else:
                flash('Failed to save credentials. Please try again.', 'error')
    
    return render_template('setup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page and authentication"""
    # Redirect to setup if not configured
    if not load_admin_credentials():
        return redirect(url_for('setup'))
    
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        
        if verify_admin_credentials(email, password):
            session['authenticated'] = True
            session['admin_email'] = email
            session.permanent = True
            flash('Successfully logged in!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password. Please try again.', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    """Logout and clear session"""
    session.clear()
    flash('Successfully logged out.', 'success')
    return redirect(url_for('login'))

# Global instances
server_manager = ServerManager()
downloader = PaperMCDownloader()

def secure_path_join(base_path: Path, user_path: str) -> Path:
    """Securely join user-provided path with base path, preventing path traversal attacks"""
    import os
    
    # Normalize inputs
    user_path = user_path.strip() if user_path else ''
    if not user_path or user_path == '/':
        return base_path
    
    # Remove leading slashes and normalize
    user_path = user_path.lstrip('/')
    
    # Reject dangerous path components immediately
    if '..' in user_path or user_path.startswith('/') or any(c in user_path for c in ['\\', '\0']):
        raise ValueError(f"Invalid path detected: {user_path}")
    
    try:
        # Create the full path and resolve
        full_path = (base_path / user_path).resolve()
        base_resolved = base_path.resolve()
        
        # Use proper containment check
        try:
            # Python 3.9+ method (preferred)
            if not full_path.is_relative_to(base_resolved):
                raise ValueError(f"Path outside base directory: {user_path}")
        except AttributeError:
            # Fallback for older Python versions
            common_path = os.path.commonpath([str(full_path), str(base_resolved)])
            if common_path != str(base_resolved):
                raise ValueError(f"Path outside base directory: {user_path}")
        
        return full_path
        
    except (ValueError, OSError) as e:
        print(f"Security: Blocked path traversal attempt - {user_path}: {e}")
        raise ValueError(f"Invalid or unsafe path: {user_path}")

# Performance monitoring
class PerformanceMonitor:
    def __init__(self):
        self.stats = {}
        self.running = False
        self.thread = None
    
    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self.thread.start()
    
    def stop(self):
        self.running = False
    
    def _monitor_loop(self):
        while self.running:
            try:
                # Get system stats
                cpu_percent = psutil.cpu_percent(interval=1)
                memory = psutil.virtual_memory()
                
                # Get server stats
                server_stats = {}
                for name, server in server_manager.servers.items():
                    status = server_manager.get_server_status(name)
                    player_count = 0
                    
                    # Try to get player count if server is running
                    if status == "running" and server.process:
                        try:
                            # This would need to be implemented based on server query
                            player_count = 0  # Placeholder
                        except:
                            pass
                    
                    server_stats[name] = {
                        'status': status,
                        'players': player_count,
                        'cpu': 0,  # Individual server CPU usage
                        'memory': 0  # Individual server memory usage
                    }
                
                # Update global stats
                self.stats = {
                    'system': {
                        'cpu': cpu_percent,
                        'memory': {
                            'used': memory.used,
                            'total': memory.total,
                            'percent': memory.percent
                        }
                    },
                    'servers': server_stats,
                    'timestamp': time.time()
                }
                
                # Emit to all connected clients
                socketio.emit('stats_update', self.stats)
                
            except Exception as e:
                print(f"Performance monitoring error: {e}")
            
            time.sleep(2)  # Update every 2 seconds

# Initialize performance monitor
perf_monitor = PerformanceMonitor()

# Record server start time for uptime calculation
start_time = time.time()



@app.route('/')
def index():
    """Main route - redirect to setup or login as needed"""
    if not load_admin_credentials():
        return redirect(url_for('setup'))
    return redirect(url_for('dashboard'))

@app.route('/dashboard')  
@login_required
def dashboard():
    """Main dashboard"""
    servers = server_manager.servers
    return render_template('dashboard.html', servers=servers)

@app.route('/api/servers')
@login_required
def api_servers():
    """Get all servers"""
    servers_data = []
    for name, server in server_manager.servers.items():
        status = server_manager.get_server_status(name)
        servers_data.append({
            'name': name,
            'status': status,
            'port': server.port,
            'path': server.path,
            'min_ram': server.min_ram,
            'max_ram': server.max_ram
        })
    return jsonify(servers_data)

@app.route('/api/server/<name>/start', methods=['POST'])
@login_required
def api_start_server(name):
    """Start a server"""
    try:
        success = server_manager.start_server(name)
        return jsonify({'success': success, 'message': f'Server {name} started' if success else 'Failed to start server'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/server/<name>/stop', methods=['POST'])
@login_required
def api_stop_server(name):
    """Stop a server"""
    try:
        success = server_manager.stop_server(name)
        return jsonify({'success': success, 'message': f'Server {name} stopped' if success else 'Failed to stop server'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/server/<name>/restart', methods=['POST'])
def api_restart_server(name):
    """Restart a server"""
    try:
        success = server_manager.restart_server(name)
        return jsonify({'success': success, 'message': f'Server {name} restarted' if success else 'Failed to restart server'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/server/create', methods=['POST'])
@login_required
def api_create_server():
    """Create a new server"""
    try:
        data = request.json
        
        required_fields = ['name', 'version', 'min_ram', 'max_ram', 'storage_path', 'port', 'storage_limit']
        for field in required_fields:
            if field not in data:
                return jsonify({'success': False, 'message': f'Missing field: {field}'})
        
        # Check if server already exists
        if data['name'] in server_manager.servers:
            return jsonify({'success': False, 'message': 'Server already exists'})
        
        # Create server
        success = server_manager.create_server(
            name=data['name'],
            version=data['version'],
            min_ram=data['min_ram'],
            max_ram=data['max_ram'],
            storage_path=data['storage_path'],
            port=data['port'],
            storage_limit=data['storage_limit']
        )
        
        if success:
            # Download files in background
            server = server_manager.servers[data['name']]
            server_path = Path(server.path)
            
            # Start background download and setup
            def download_and_setup_files():
                try:
                    downloader.download_server_files(data['version'], server_path)
                    if data.get('install_plugins', False):
                        plugins_path = server_path / 'plugins'
                        downloader.download_basic_plugin_pack(plugins_path)
                    
                    # Enable Playit.gg if requested
                    if data.get('enable_playit', False):
                        try:
                            from playit_manager import playit_manager
                            print(f"🌐 Enabling Playit.gg for server '{data['name']}'...")
                            playit_success = playit_manager.enable_playit_for_server(data['name'], data['port'])
                            if playit_success:
                                print(f"✅ Playit.gg enabled for server '{data['name']}'!")
                            else:
                                print(f"❌ Failed to enable Playit.gg for server '{data['name']}'")
                        except Exception as e:
                            print(f"Playit integration error: {e}")
                            
                except Exception as e:
                    print(f"Download/setup error: {e}")
            
            thread = threading.Thread(target=download_and_setup_files, daemon=True)
            thread.start()
            
            return jsonify({'success': True, 'message': f'Server {data["name"]} created successfully'})
        else:
            return jsonify({'success': False, 'message': 'Failed to create server'})
            
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/server/<name>')
def server_management(name):
    """Server management page"""
    if name not in server_manager.servers:
        return redirect(url_for('dashboard'))
    
    server = server_manager.servers[name]
    return render_template('server_management.html', server=server)

@app.route('/api/server/<name>/console')
def api_server_console(name):
    """Get server console output"""
    try:
        if name not in server_manager.servers:
            return jsonify({'success': False, 'message': 'Server not found'})
        
        server = server_manager.servers[name]
        # Get console output from server
        try:
            console_output = server_manager.get_console_output(name) or "No console output available"
        except:
            console_output = "Console output temporarily unavailable"
        
        # Join list of strings into single string for frontend
        if isinstance(console_output, list):
            console_string = '\n'.join(console_output)
        else:
            console_string = str(console_output) if console_output else "No console output available"
            
        return jsonify({
            'success': True,
            'output': console_string
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/server/<name>/command', methods=['POST'])
def api_server_command(name):
    """Send command to server"""
    try:
        if name not in server_manager.servers:
            return jsonify({'success': False, 'message': 'Server not found'})
        
        data = request.json
        command = data.get('command', '').strip()
        
        if not command:
            return jsonify({'success': False, 'message': 'No command provided'})
        
        success = server_manager.send_command(name, command)
        return jsonify({
            'success': success,
            'message': f'Command sent: {command}' if success else 'Failed to send command'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/server/<name>/delete', methods=['DELETE'])
@login_required
def api_server_delete(name):
    """Delete a server"""
    try:
        
        # Validate server name
        if not validate_server_name(name):
            return jsonify({'success': False, 'message': 'Invalid server name or server not found'}), 400
        
        # Check if server is running and stop it first
        server_status = server_manager.get_server_status(name)
        if server_status == "running":
            server_manager.stop_server(name)
            # Wait a bit for server to stop
            import time
            time.sleep(2)
        
        # Delete server
        success = server_manager.delete_server(name)
        
        if success:
            return jsonify({'success': True, 'message': f'Server {name} deleted successfully'})
        else:
            return jsonify({'success': False, 'message': 'Failed to delete server'}), 500
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'Server deletion failed: {str(e)}'}), 500

# File Management API
@app.route('/api/server/<name>/files')
def api_server_files(name):
    """Get server files list"""
    try:
        if name not in server_manager.servers:
            return jsonify({'success': False, 'message': 'Server not found'})
        
        server = server_manager.servers[name]
        path = request.args.get('path', '/')
        
        # Securely resolve full path
        server_base = Path(server.path)
        full_path = secure_path_join(server_base, path)
        
        if not full_path.exists():
            return jsonify({'success': False, 'message': 'Path not found'})
        
        files = []
        for item in full_path.iterdir():
            try:
                stat = item.stat()
                files.append({
                    'name': item.name,
                    'type': 'directory' if item.is_dir() else 'file',
                    'size': stat.st_size if item.is_file() else 0,
                    'modified': time.strftime('%Y-%m-%d %H:%M', time.localtime(stat.st_mtime))
                })
            except (OSError, PermissionError):
                continue
        
        # Sort: directories first, then files
        files.sort(key=lambda x: (x['type'] == 'file', x['name'].lower()))
        
        return jsonify({'success': True, 'files': files})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/server/<name>/upload', methods=['POST'])
def api_server_upload(name):
    """Upload file to server"""
    try:
        if name not in server_manager.servers:
            return jsonify({'success': False, 'message': 'Server not found'})
        
        server = server_manager.servers[name]
        file = request.files.get('file')
        path = request.form.get('path', '/')
        
        if not file:
            return jsonify({'success': False, 'message': 'No file provided'})
        
        # Securely resolve upload path and filename
        from werkzeug.utils import secure_filename
        
        server_base = Path(server.path)
        upload_path = secure_path_join(server_base, path)
        
        # Sanitize filename to prevent path traversal
        safe_filename = secure_filename(file.filename)
        if not safe_filename or '/' in safe_filename or '\\' in safe_filename:
            return jsonify({'success': False, 'message': 'Invalid filename'})
        
        upload_path.mkdir(parents=True, exist_ok=True)
        file_path = upload_path / safe_filename
        
        file.save(str(file_path))
        
        return jsonify({'success': True, 'message': f'Uploaded {file.filename}'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/server/<name>/download')
def api_server_download(name):
    """Download file from server"""
    try:
        if name not in server_manager.servers:
            return jsonify({'success': False, 'message': 'Server not found'})
        
        server = server_manager.servers[name]
        file_path = request.args.get('path', '')
        
        # Securely resolve file path
        try:
            server_base = Path(server.path)
            full_path = secure_path_join(server_base, file_path)
        except ValueError as e:
            return jsonify({'success': False, 'message': 'Invalid file path'})
        
        if not full_path.exists() or not full_path.is_file():
            return jsonify({'success': False, 'message': 'File not found'})
        
        from flask import send_file
        return send_file(str(full_path), as_attachment=True)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/server/<name>/file/content')
def api_server_file_content(name):
    """Get file content for editing"""
    try:
        if name not in server_manager.servers:
            return jsonify({'success': False, 'message': 'Server not found'})
        
        server = server_manager.servers[name]
        file_path = request.args.get('path', '')
        
        # Securely resolve file path
        try:
            server_base = Path(server.path)
            full_path = secure_path_join(server_base, file_path)
        except ValueError as e:
            return jsonify({'success': False, 'message': 'Invalid file path'})
        
        if not full_path.exists() or not full_path.is_file():
            return jsonify({'success': False, 'message': 'File not found'})
        
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            return jsonify({'success': False, 'message': 'File is not text-readable'})
        
        return jsonify({'success': True, 'content': content})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/server/<name>/file/save', methods=['POST'])
def api_server_file_save(name):
    """Save file content"""
    try:
        if name not in server_manager.servers:
            return jsonify({'success': False, 'message': 'Server not found'})
        
        server = server_manager.servers[name]
        data = request.json
        file_path = data.get('path', '')
        content = data.get('content', '')
        
        # Securely resolve file path
        try:
            server_base = Path(server.path)
            full_path = secure_path_join(server_base, file_path)
        except ValueError as e:
            return jsonify({'success': False, 'message': 'Invalid file path'})
        
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return jsonify({'success': True, 'message': 'File saved'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/server/<name>/file/delete', methods=['POST'])
def api_server_file_delete(name):
    """Delete file or directory"""
    try:
        if name not in server_manager.servers:
            return jsonify({'success': False, 'message': 'Server not found'})
        
        server = server_manager.servers[name]
        data = request.json
        file_path = data.get('path', '')
        
        # Securely resolve file path
        try:
            server_base = Path(server.path)
            full_path = secure_path_join(server_base, file_path)
        except ValueError as e:
            return jsonify({'success': False, 'message': 'Invalid file path'})
        
        if not full_path.exists():
            return jsonify({'success': False, 'message': 'File not found'})
        
        if full_path.is_dir():
            import shutil
            shutil.rmtree(full_path)
        else:
            full_path.unlink()
        
        return jsonify({'success': True, 'message': 'Deleted successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/server/<name>/file/rename', methods=['POST'])
def api_server_file_rename(name):
    """Rename file or directory"""
    try:
        if name not in server_manager.servers:
            return jsonify({'success': False, 'message': 'Server not found'})
        
        server = server_manager.servers[name]
        data = request.json
        old_path = data.get('oldPath', '')
        new_name = data.get('newName', '')
        
        if old_path.startswith('/'):
            full_old_path = Path(server.path) / old_path.lstrip('/')
        else:
            full_old_path = Path(server.path) / old_path
        
        if not full_old_path.exists():
            return jsonify({'success': False, 'message': 'File not found'})
        
        new_path = full_old_path.parent / new_name
        full_old_path.rename(new_path)
        
        return jsonify({'success': True, 'message': 'Renamed successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/server/<name>/file/mkdir', methods=['POST'])
def api_server_file_mkdir(name):
    """Create directory"""
    try:
        if name not in server_manager.servers:
            return jsonify({'success': False, 'message': 'Server not found'})
        
        server = server_manager.servers[name]
        data = request.json
        dir_path = data.get('path', '')
        
        # Securely resolve directory path
        try:
            server_base = Path(server.path)
            full_path = secure_path_join(server_base, dir_path)
        except ValueError as e:
            return jsonify({'success': False, 'message': 'Invalid directory path'})
        
        full_path.mkdir(parents=True, exist_ok=True)
        
        return jsonify({'success': True, 'message': 'Directory created'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    print('Client connected')
    emit('connected', {'message': 'Connected to CrazeDyn Panel'})

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnect"""
    print('Client disconnected')

@socketio.on('subscribe_server')
def handle_subscribe_server(data):
    """Subscribe to server updates"""
    server_name = data.get('server_name')
    if server_name in server_manager.servers:
        # Join room for this server
        join_room(server_name)
        emit('subscribed', {'server': server_name})

# Plugin Management API
@app.route('/api/paper/versions')
def api_paper_versions():
    """Get all available PaperMC versions with download links"""
    try:
        versions = downloader.get_paper_versions()
        # Convert to list with version and download URL
        version_list = []
        for version, download_url in versions.items():
            version_list.append({
                'version': version,
                'download_url': download_url,
                'display_name': f"Paper {version}"
            })
        
        return jsonify({
            'success': True,
            'versions': version_list,
            'latest': version_list[0]['version'] if version_list else '1.21.8'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e), 'versions': []})

@app.route('/api/server/<name>/plugins')
def api_server_plugins(name):
    """Get installed plugins list"""
    try:
        if name not in server_manager.servers:
            return jsonify({'success': False, 'message': 'Server not found'})
        
        server = server_manager.servers[name]
        plugins_path = Path(server.path) / 'plugins'
        
        if not plugins_path.exists():
            return jsonify({'success': True, 'plugins': []})
        
        plugins = []
        for plugin_file in plugins_path.glob('*.jar'):
            try:
                stat = plugin_file.stat()
                size = stat.st_size
                size_str = f"{size / 1024:.1f} KB" if size < 1024*1024 else f"{size / (1024*1024):.1f} MB"
                
                plugins.append({
                    'name': plugin_file.stem,
                    'filename': plugin_file.name,
                    'size': size_str,
                    'status': 'Enabled',
                    'version': 'Unknown'  # Could be extracted from plugin.yml if needed
                })
            except (OSError, PermissionError):
                continue
        
        return jsonify({'success': True, 'plugins': plugins})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/server/<name>/plugins/upload', methods=['POST'])
def api_server_plugin_upload(name):
    """Upload plugin to server"""
    try:
        if name not in server_manager.servers:
            return jsonify({'success': False, 'message': 'Server not found'})
        
        server = server_manager.servers[name]
        plugin_file = request.files.get('plugin')
        
        if not plugin_file:
            return jsonify({'success': False, 'message': 'No plugin file provided'})
        
        if not plugin_file.filename.endswith('.jar'):
            return jsonify({'success': False, 'message': 'Only .jar files are allowed'})
        
        plugins_path = Path(server.path) / 'plugins'
        plugins_path.mkdir(exist_ok=True)
        
        file_path = plugins_path / plugin_file.filename
        plugin_file.save(str(file_path))
        
        return jsonify({'success': True, 'message': f'Uploaded {plugin_file.filename}'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/server/<name>/plugins/delete', methods=['POST'])
def api_server_plugin_delete(name):
    """Delete plugin from server"""
    try:
        if name not in server_manager.servers:
            return jsonify({'success': False, 'message': 'Server not found'})
        
        server = server_manager.servers[name]
        data = request.json
        plugin_name = data.get('plugin_name', '')
        
        plugins_path = Path(server.path) / 'plugins'
        plugin_file = plugins_path / f'{plugin_name}.jar'
        
        if not plugin_file.exists():
            # Try to find by exact filename
            for jar_file in plugins_path.glob('*.jar'):
                if jar_file.stem == plugin_name:
                    plugin_file = jar_file
                    break
        
        if plugin_file.exists():
            plugin_file.unlink()
            return jsonify({'success': True, 'message': f'Deleted {plugin_name}'})
        else:
            return jsonify({'success': False, 'message': 'Plugin not found'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/server/<name>/plugins/install', methods=['POST'])
def api_server_plugin_install(name):
    """Install plugin from SpigotMC"""
    try:
        if name not in server_manager.servers:
            return jsonify({'success': False, 'message': 'Server not found'})
        
        server = server_manager.servers[name]
        data = request.json
        plugin_id = data.get('plugin_id', '')
        plugin_name = data.get('plugin_name', '')
        
        plugins_path = Path(server.path) / 'plugins'
        plugins_path.mkdir(exist_ok=True)
        
        # Use the existing spigot browser to download
        try:
            from app.core.spigot_browser import PluginInfo
            plugin_info = PluginInfo(
                id=plugin_id,
                name=plugin_name,
                download_url=f'https://api.spiget.org/v2/resources/{plugin_id}/download',
                premium=False
            )
            
            success = downloader.spigot_browser.download_plugin(plugin_info, plugins_path)
            
            if success:
                return jsonify({'success': True, 'message': f'Installed {plugin_name}'})
            else:
                return jsonify({'success': False, 'message': f'Failed to download {plugin_name}'})
        except Exception as download_error:
            return jsonify({'success': False, 'message': f'Download error: {str(download_error)}'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

# Plugin Browser API
@app.route('/api/plugins/search')
def api_plugins_search():
    """Search plugins on SpigotMC"""
    try:
        query = request.args.get('query', '')
        category = request.args.get('category', '')
        sort = request.args.get('sort', 'downloads')
        
        # Use existing spigot browser
        plugins, _ = downloader.spigot_browser.search_plugins(
            query=query,
            category=category if category else None,
            sort=sort,
            size=30
        )
        
        return jsonify({'success': True, 'plugins': [plugin.__dict__ for plugin in plugins]})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/plugins/popular')
def api_plugins_popular():
    """Get popular plugins"""
    try:
        plugins = downloader.spigot_browser.get_popular_plugins(20)
        return jsonify({'success': True, 'plugins': [plugin.__dict__ for plugin in plugins]})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/plugins/details/<plugin_id>')
def api_plugin_details(plugin_id):
    """Get plugin details"""
    try:
        plugin = downloader.spigot_browser.get_plugin_details(plugin_id)
        if plugin:
            return jsonify({'success': True, 'plugin': plugin.__dict__})
        else:
            return jsonify({'success': False, 'message': 'Plugin not found'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

def get_local_ip():
    """Get local IP address for external access"""
    try:
        import socket
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        return local_ip
    except:
        return '127.0.0.1'

if __name__ == '__main__':
    # Start performance monitoring
    perf_monitor.start()
    
    # Get local IP for external access
    local_ip = get_local_ip()
    port = 5000
    
    print("=" * 50)
    print("🚀 CrazeDyn Web Panel Starting")
    print("=" * 50)
    print(f"📱 Local Access:    http://localhost:{port}")
    print(f"🌐 Network Access:  http://{local_ip}:{port}")
    print("=" * 50)
    print("✨ Features:")
    print("   • Real-time server monitoring")
    print("   • Remote server management")
    print("   • Live console access")
    print("   • Responsive modern UI")
    print("=" * 50)
    
    # Run the web server
    try:
        socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        print("\n🛑 Shutting down CrazeDyn Panel...")
        perf_monitor.stop()
    except Exception as e:
        print(f"❌ Error starting server: {e}")

# Initialize SpigotMC browser for downloader
if not hasattr(downloader, 'spigot_browser'):
    try:
        from app.core.spigot_browser import SpigotMCBrowser
        downloader.spigot_browser = SpigotMCBrowser()
        print("✅ SpigotMC browser initialized successfully")
    except Exception as e:
        print(f"Warning: Could not initialize SpigotMC browser: {e}")
        # Create a dummy browser for development
        class DummyBrowser:
            def search_plugins(self, **kwargs):
                return [], 0
            def get_popular_plugins(self, count):
                return []
            def get_plugin_details(self, plugin_id):
                return None
            def download_plugin(self, plugin, destination):
                return False
        downloader.spigot_browser = DummyBrowser()
        
# Update routes for new file management interface
@app.route('/server/<name>/files')
def server_files(name):
    """Server file management page"""
    if name not in server_manager.servers:
        return redirect(url_for('dashboard'))
    
    server = server_manager.servers[name]
    return render_template('files.html', server=server)

@app.route('/server/<name>/plugins')
def server_plugins(name):
    """Server plugin management page"""
    if name not in server_manager.servers:
        return redirect(url_for('dashboard'))
    
    server = server_manager.servers[name]
    return render_template('plugins.html', server=server)

@app.route('/servers')
def servers_list():
    """Servers list page"""
    servers = server_manager.servers
    return render_template('dashboard.html', servers=servers)

@app.route('/plugins')
def global_plugins():
    """Global plugins management page"""
    return render_template('global_plugins.html')

@app.route('/settings')
def settings_page():
    """Settings page"""
    return render_template('settings.html')