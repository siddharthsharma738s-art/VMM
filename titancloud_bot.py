import discord
from discord.ext import commands
from discord import ui, app_commands
import os
import random
import string
import json
import subprocess
from dotenv import load_dotenv
import asyncio
import datetime
import docker
import time
import logging
import traceback
import aiohttp
import socket
import re
import psutil
import platform
import shutil
from typing import Optional, Literal
import sqlite3
import pickle
import base64
import threading
from flask import Flask, render_template, request, jsonify, session
from flask_socketio import SocketIO, emit
import docker
import paramiko
import os
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('titancloud_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('TitanCloudBot')

# Load environment variables
load_dotenv()

# Bot configuration
TOKEN = (os.getenv('DISCORD_TOKEN') or '').strip()
OWNER_IDS = {
    int(id_.strip())
    for id_ in os.getenv(
        'OWNER_IDS',
        '1159164727323271320,1103109628591620106'
    ).split(',')
    if id_.strip()
}
OWNER_ROLE_ID = int(os.getenv('OWNER_ROLE_ID', '1426555185454649447'))
USER_ROLE_ID = int(os.getenv('USER_ROLE_ID', '1536277694927474720'))
# Keep ADMIN_IDS as the runtime admin set for backwards-compatible admin commands.
ADMIN_IDS = set(OWNER_IDS)
BRAND_NAME = "𝐓𝐢𝐭𝐚𝐧𝐂𝐥𝐨𝐮𝐝™ |"
WATERMARK = "TitanCloud VPS Service"
WELCOME_MESSAGE = "Welcome to TitanCloud. Your private compute instance is ready."
MAX_VPS_PER_USER = 1
VPS_MEMORY_GB = 32
DEFAULT_CPU_CORES = int(os.getenv('DEFAULT_CPU_CORES', '6'))
DEFAULT_DISK_GB = int(os.getenv('DEFAULT_DISK_GB', '80'))
DEFAULT_OS_IMAGE = os.getenv('DEFAULT_OS_IMAGE', 'ubuntu:22.04')
DOCKER_NETWORK = os.getenv('DOCKER_NETWORK', 'bridge')
MAX_CONTAINERS = int(os.getenv('MAX_CONTAINERS', '250'))
DB_FILE = 'titancloud.db'
BACKUP_FILE = 'titancloud_backup.pkl'
LEGACY_DB_FILE = ''.join(('light', 'plays.db'))
if not os.path.exists(DB_FILE) and os.path.exists(LEGACY_DB_FILE):
    shutil.copy2(LEGACY_DB_FILE, DB_FILE)

# Premium, text-symbol-only visual language used throughout the Discord UI.
BRAND_COLOR = discord.Color.from_rgb(88, 101, 242)
SUCCESS_COLOR = discord.Color.from_rgb(46, 204, 113)
WARNING_COLOR = discord.Color.from_rgb(241, 196, 15)
ERROR_COLOR = discord.Color.from_rgb(231, 76, 60)
EMOJI_VERIFY = "<a:8349verify:1536290099526901850>"
EMOJI_DECLINE = "<a:8349verify:1536290099526901850>"
EMOJI_LOADING = "<a:48084loadingcircle:1536290034930548786>"
EMOJI_RESTART = "<:9942warning:1536290027955290122>"
EMOJI_START = "<a:green_loading:1536292254195650601>"
EMOJI_STOP = "<:red_dot:1536292733528838195>"
EMOJI_MANAGE = "<:30208managementhexagon:1536290031398817824>"
SYMBOL = {
    'success': EMOJI_VERIFY,
    'error': EMOJI_DECLINE,
    'info': EMOJI_MANAGE,
    'progress': EMOJI_LOADING,
    'status': EMOJI_MANAGE,
}


def premium_embed(title, description=None, color=BRAND_COLOR):
    """Create a clean, consistently branded TitanCloud embed."""
    embed = discord.Embed(title=f"{BRAND_NAME} {title}", description=description, color=color)
    embed.set_footer(text="TitanCloud  •  Private compute, controlled from Discord")
    embed.timestamp = discord.utils.utcnow()
    return embed

# Known miner process names/patterns
MINER_PATTERNS = [
    'xmrig', 'ethminer', 'cgminer', 'sgminer', 'bfgminer',
    'minerd', 'cpuminer', 'cryptonight', 'stratum+tcp'
]

# Dockerfile template for custom images
DOCKERFILE_TEMPLATE = """
FROM {base_image}

# Prevent prompts
ENV DEBIAN_FRONTEND=noninteractive

# Install systemd, sudo, SSH, Docker and other essential packages
RUN apt-get update && \\
    apt-get install -y systemd systemd-sysv dbus sudo \\
                       curl gnupg2 apt-transport-https ca-certificates \\
                       software-properties-common \\
                       docker.io openssh-server tmate && \\
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Root password
RUN echo "root:{root_password}" | chpasswd

# Create user and set password
RUN useradd -m -s /bin/bash {username} && \\
    echo "{username}:{user_password}" | chpasswd && \\
    usermod -aG sudo {username}

# Enable SSH login
RUN mkdir /var/run/sshd && \\
    sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config && \\
    sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/' /etc/ssh/sshd_config

# Enable services on boot
RUN systemctl enable ssh && \\
    systemctl enable docker

# TitanCloud customization
RUN echo '{welcome_message}' > /etc/motd && \\
    echo 'echo "{welcome_message}"' >> /home/{username}/.bashrc && \\
    echo '{watermark}' > /etc/machine-info && \\
    echo 'titancloud-{vps_id}' > /etc/hostname

# Install additional useful packages
RUN apt-get update && \\
    apt-get install -y neofetch htop nano vim wget git tmux net-tools dnsutils iputils-ping && \\
    apt-get clean && \\
    rm -rf /var/lib/apt/lists/*

# Fix systemd inside container
STOPSIGNAL SIGRTMIN+3

# Boot into systemd (like a VM)
CMD ["/sbin/init"]
"""

class Database:
    """Handles all data persistence using SQLite3"""
    def __init__(self, db_file):
        self.conn = sqlite3.connect(db_file, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._create_tables()
        self._initialize_settings()

    def _create_tables(self):
        """Create necessary tables"""
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS vps_instances (
                token TEXT PRIMARY KEY,
                vps_id TEXT UNIQUE,
                container_id TEXT,
                memory INTEGER,
                cpu INTEGER,
                disk INTEGER,
                username TEXT,
                password TEXT,
                root_password TEXT,
                created_by TEXT,
                created_at TEXT,
                tmate_session TEXT,
                watermark TEXT,
                os_image TEXT,
                restart_count INTEGER DEFAULT 0,
                last_restart TEXT,
                status TEXT DEFAULT 'running',
                use_custom_image BOOLEAN DEFAULT 1
            )
        ''')

        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS usage_stats (
                key TEXT PRIMARY KEY,
                value INTEGER DEFAULT 0
            )
        ''')

        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')

        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id TEXT PRIMARY KEY
            )
        ''')

        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS admin_users (
                user_id TEXT PRIMARY KEY
            )
        ''')

        self.conn.commit()

    def _initialize_settings(self):
        """Initialize default settings"""
        defaults = {
            'max_containers': str(MAX_CONTAINERS),
            'max_vps_per_user': str(MAX_VPS_PER_USER)
        }
        for key, value in defaults.items():
            self.cursor.execute('INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)', (key, value))

        # The service plan is intentionally fixed at one container per user.
        # Replace an older database value (the upstream default was three).
        self.cursor.execute(
            'INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, ?)',
            ('max_vps_per_user', str(MAX_VPS_PER_USER))
        )
        self.cursor.execute(
            'INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, ?)',
            ('max_containers', str(MAX_CONTAINERS))
        )

        # Load admin users from database
        self.cursor.execute('SELECT user_id FROM admin_users')
        for row in self.cursor.fetchall():
            ADMIN_IDS.add(int(row[0]))

        self.conn.commit()

    def get_setting(self, key, default=None):
        self.cursor.execute('SELECT value FROM system_settings WHERE key = ?', (key,))
        result = self.cursor.fetchone()
        return int(result[0]) if result else default

    def set_setting(self, key, value):
        self.cursor.execute('INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, ?)', (key, str(value)))
        self.conn.commit()

    def get_stat(self, key, default=0):
        self.cursor.execute('SELECT value FROM usage_stats WHERE key = ?', (key,))
        result = self.cursor.fetchone()
        return result[0] if result else default

    def increment_stat(self, key, amount=1):
        current = self.get_stat(key)
        self.cursor.execute('INSERT OR REPLACE INTO usage_stats (key, value) VALUES (?, ?)', (key, current + amount))
        self.conn.commit()

    def get_vps_by_id(self, vps_id):
        self.cursor.execute('SELECT * FROM vps_instances WHERE vps_id = ?', (vps_id,))
        row = self.cursor.fetchone()
        if not row:
            return None, None
        columns = [desc[0] for desc in self.cursor.description]
        vps = dict(zip(columns, row))
        return vps['token'], vps

    def get_vps_by_token(self, token):
        self.cursor.execute('SELECT * FROM vps_instances WHERE token = ?', (token,))
        row = self.cursor.fetchone()
        if not row:
            return None
        columns = [desc[0] for desc in self.cursor.description]
        return dict(zip(columns, row))

    def get_user_vps_count(self, user_id):
        self.cursor.execute('SELECT COUNT(*) FROM vps_instances WHERE created_by = ?', (str(user_id),))
        return self.cursor.fetchone()[0]

    def get_user_vps(self, user_id):
        self.cursor.execute('SELECT * FROM vps_instances WHERE created_by = ?', (str(user_id),))
        columns = [desc[0] for desc in self.cursor.description]
        return [dict(zip(columns, row)) for row in self.cursor.fetchall()]

    def get_all_vps(self):
        self.cursor.execute('SELECT * FROM vps_instances')
        columns = [desc[0] for desc in self.cursor.description]
        return {row[0]: dict(zip(columns, row)) for row in self.cursor.fetchall()}

    def add_vps(self, vps_data):
        columns = ', '.join(vps_data.keys())
        placeholders = ', '.join('?' for _ in vps_data)
        self.cursor.execute(f'INSERT INTO vps_instances ({columns}) VALUES ({placeholders})', tuple(vps_data.values()))
        self.conn.commit()
        self.increment_stat('total_vps_created')

    def remove_vps(self, token):
        self.cursor.execute('DELETE FROM vps_instances WHERE token = ?', (token,))
        self.conn.commit()
        return self.cursor.rowcount > 0

    def update_vps(self, token, updates):
        set_clause = ', '.join(f'{k} = ?' for k in updates)
        values = list(updates.values()) + [token]
        self.cursor.execute(f'UPDATE vps_instances SET {set_clause} WHERE token = ?', values)
        self.conn.commit()
        return self.cursor.rowcount > 0

    def is_user_banned(self, user_id):
        self.cursor.execute('SELECT 1 FROM banned_users WHERE user_id = ?', (str(user_id),))
        return self.cursor.fetchone() is not None

    def ban_user(self, user_id):
        self.cursor.execute('INSERT OR IGNORE INTO banned_users (user_id) VALUES (?)', (str(user_id),))
        self.conn.commit()

    def unban_user(self, user_id):
        self.cursor.execute('DELETE FROM banned_users WHERE user_id = ?', (str(user_id),))
        self.conn.commit()

    def get_banned_users(self):
        self.cursor.execute('SELECT user_id FROM banned_users')
        return [row[0] for row in self.cursor.fetchall()]

    def add_admin(self, user_id):
        self.cursor.execute('INSERT OR IGNORE INTO admin_users (user_id) VALUES (?)', (str(user_id),))
        self.conn.commit()
        ADMIN_IDS.add(int(user_id))

    def remove_admin(self, user_id):
        self.cursor.execute('DELETE FROM admin_users WHERE user_id = ?', (str(user_id),))
        self.conn.commit()
        if int(user_id) in ADMIN_IDS:
            ADMIN_IDS.remove(int(user_id))

    def get_admins(self):
        self.cursor.execute('SELECT user_id FROM admin_users')
        return [row[0] for row in self.cursor.fetchall()]

    def backup_data(self):
        """Backup all data to a file"""
        data = {
            'vps_instances': self.get_all_vps(),
            'usage_stats': {},
            'system_settings': {},
            'banned_users': self.get_banned_users(),
            'admin_users': self.get_admins()
        }

        # Get usage stats
        self.cursor.execute('SELECT * FROM usage_stats')
        for row in self.cursor.fetchall():
            data['usage_stats'][row[0]] = row[1]

        # Get system settings
        self.cursor.execute('SELECT * FROM system_settings')
        for row in self.cursor.fetchall():
            data['system_settings'][row[0]] = row[1]

        with open(BACKUP_FILE, 'wb') as f:
            pickle.dump(data, f)

        return True

    def restore_data(self):
        """Restore data from backup file"""
        if not os.path.exists(BACKUP_FILE):
            return False

        try:
            with open(BACKUP_FILE, 'rb') as f:
                data = pickle.load(f)

            # Clear all tables
            self.cursor.execute('DELETE FROM vps_instances')
            self.cursor.execute('DELETE FROM usage_stats')
            self.cursor.execute('DELETE FROM system_settings')
            self.cursor.execute('DELETE FROM banned_users')
            self.cursor.execute('DELETE FROM admin_users')

            # Restore VPS instances
            for token, vps in data['vps_instances'].items():
                columns = ', '.join(vps.keys())
                placeholders = ', '.join('?' for _ in vps)
                self.cursor.execute(f'INSERT INTO vps_instances ({columns}) VALUES ({placeholders})', tuple(vps.values()))

            # Restore usage stats
            for key, value in data['usage_stats'].items():
                self.cursor.execute('INSERT INTO usage_stats (key, value) VALUES (?, ?)', (key, value))

            # Restore system settings
            for key, value in data['system_settings'].items():
                self.cursor.execute('INSERT INTO system_settings (key, value) VALUES (?, ?)', (key, value))

            # Restore banned users
            for user_id in data['banned_users']:
                self.cursor.execute('INSERT INTO banned_users (user_id) VALUES (?)', (user_id,))

            # Restore admin users
            for user_id in data['admin_users']:
                self.cursor.execute('INSERT INTO admin_users (user_id) VALUES (?)', (user_id,))
                ADMIN_IDS.add(int(user_id))

            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error restoring data: {e}")
            return False

    def close(self):
        self.conn.close()

# Initialize bot with command prefix '/'
class TitanCloudBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.db = Database(DB_FILE)
        self.session = None
        self.docker_client = None
        self.system_stats = {
            'cpu_usage': 0,
            'memory_usage': 0,
            'disk_usage': 0,
            'network_io': (0, 0),
            'last_updated': 0
        }
        self.my_persistent_views = {}
        # Keep Discord responsive for large guilds while bounding heavy Docker
        # work. The active-user set prevents duplicate regular-user requests.
        self.provision_semaphore = asyncio.Semaphore(4)
        self.provision_guard = asyncio.Lock()
        self.provisioning_users = set()
        self.ready_once = False
        self.background_tasks = []

    async def setup_hook(self):
        self.session = aiohttp.ClientSession()
        try:
            self.docker_client = docker.from_env()
            logger.info("Docker client initialized successfully")
            self.background_tasks = [
                asyncio.create_task(
                    self.supervise_background_task("system statistics", self.update_system_stats)
                ),
                asyncio.create_task(
                    self.supervise_background_task("anti-miner monitor", self.anti_miner_monitor)
                )
            ]
            # Reconnect to existing containers
            await self.reconnect_containers()
            # Restore persistent views
            await self.restore_persistent_views()
        except Exception as e:
            logger.error(f"Failed to initialize Docker client: {e}")
            self.docker_client = None

    async def supervise_background_task(self, name, worker):
        """Restart a background worker if an unexpected exception escapes it."""
        while not self.is_closed():
            try:
                await worker()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(f"Background task '{name}' crashed; restarting in 5 seconds")
            if not self.is_closed():
                await asyncio.sleep(5)

    async def reconnect_containers(self):
        """Reconnect to existing containers on startup"""
        if not self.docker_client:
            return

        for token, vps in list(self.db.get_all_vps().items()):
            if vps['status'] == 'running':
                try:
                    container = await docker_call(self.docker_client.containers.get, vps['container_id'])
                    # Normalize containers created by older versions to the
                    # current fixed 32 GB service plan.
                    if vps.get('memory') != VPS_MEMORY_GB:
                        await docker_call(container.update, mem_limit=VPS_MEMORY_GB * 1024 ** 3)
                        self.db.update_vps(token, {'memory': VPS_MEMORY_GB})
                    if container.status != 'running':
                        await docker_call(container.start)
                    logger.info(f"Reconnected and started container for VPS {vps['vps_id']}")
                except docker.errors.NotFound:
                    logger.warning(f"Container {vps['container_id']} not found, removing from data")
                    self.db.remove_vps(token)
                except Exception as e:
                    logger.error(f"Error reconnecting container {vps['vps_id']}: {e}")

    async def restore_persistent_views(self):
        """Restore persistent views after restart"""
        # This would be implemented to restore any persistent UI components
        pass

    async def anti_miner_monitor(self):
        """Periodically check for mining activities"""
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                for token, vps in self.db.get_all_vps().items():
                    if vps['status'] != 'running':
                        continue
                    try:
                        container = await docker_call(self.docker_client.containers.get, vps['container_id'])
                        if container.status != 'running':
                            continue

                        # Check processes
                        exec_result = await docker_call(container.exec_run, "ps aux")
                        output = exec_result.output.decode().lower()

                        for pattern in MINER_PATTERNS:
                            if pattern in output:
                                logger.warning(f"Mining detected in VPS {vps['vps_id']}, suspending...")
                                await docker_call(container.stop)
                                self.db.update_vps(token, {'status': 'suspended'})
                                # Notify owner
                                try:
                                    owner = await self.fetch_user(int(vps['created_by']))
                                    await owner.send(f"[!] Your VPS {vps['vps_id']} has been suspended due to detected mining activity. Contact admin to unsuspend.")
                                except:
                                    pass
                                break
                    except Exception as e:
                        logger.error(f"Error checking VPS {vps['vps_id']} for mining: {e}")
            except Exception as e:
                logger.error(f"Error in anti_miner_monitor: {e}")
            await asyncio.sleep(300)  # Check every 5 minutes

    async def update_system_stats(self):
        """Update system statistics periodically"""
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                # CPU usage
                cpu_percent = await asyncio.to_thread(psutil.cpu_percent, 1)

                # Memory usage
                mem = psutil.virtual_memory()

                # Disk usage
                disk = psutil.disk_usage('/')

                # Network IO
                net_io = psutil.net_io_counters()

                self.system_stats = {
                    'cpu_usage': cpu_percent,
                    'memory_usage': mem.percent,
                    'memory_used': mem.used / (1024 ** 3),  # GB
                    'memory_total': mem.total / (1024 ** 3),  # GB
                    'disk_usage': disk.percent,
                    'disk_used': disk.used / (1024 ** 3),  # GB
                    'disk_total': disk.total / (1024 ** 3),  # GB
                    'network_sent': net_io.bytes_sent / (1024 ** 2),  # MB
                    'network_recv': net_io.bytes_recv / (1024 ** 2),  # MB
                    'last_updated': time.time()
                }
            except Exception as e:
                logger.error(f"Error updating system stats: {e}")
            await asyncio.sleep(30)

    async def close(self):
        for task in self.background_tasks:
            task.cancel()
        if self.background_tasks:
            await asyncio.gather(*self.background_tasks, return_exceptions=True)
        await super().close()
        if self.session:
            await self.session.close()
        if self.docker_client:
            self.docker_client.close()
        self.db.close()

def generate_token():
    """Generate a random token for VPS access"""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=24))

def generate_vps_id():
    """Generate a unique VPS ID"""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))

def generate_ssh_password():
    """Generate a random SSH password"""
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(random.choices(chars, k=16))

def _actor_and_roles(ctx):
    """Return the Discord actor and their roles for a context or interaction."""
    if isinstance(ctx, discord.Interaction):
        return ctx.user, getattr(ctx.user, 'roles', [])
    return ctx.author, getattr(ctx.author, 'roles', [])


def is_owner(ctx):
    """Check the configured immutable owner IDs and owner role."""
    actor, roles = _actor_and_roles(ctx)
    if actor.id in OWNER_IDS:
        return True
    return any(role.id == OWNER_ROLE_ID for role in roles)


def has_admin_role(ctx):
    """Check if the actor is an owner or a runtime admin."""
    actor, _ = _actor_and_roles(ctx)
    return actor.id in ADMIN_IDS or is_owner(ctx)


def has_user_role(ctx):
    """Allow service users, owners, and members with the owner role."""
    if is_owner(ctx):
        return True
    _, roles = _actor_and_roles(ctx)
    return any(role.id == USER_ROLE_ID for role in roles)


def is_service_user(member):
    """Check whether a specific member may own a service container."""
    if member.id in OWNER_IDS:
        return True
    role_ids = {role.id for role in getattr(member, 'roles', [])}
    return USER_ROLE_ID in role_ids or OWNER_ROLE_ID in role_ids


async def defer_context(ctx, ephemeral=False):
    """Acknowledge a slash command before Docker work exceeds Discord's deadline."""
    interaction = getattr(ctx, 'interaction', None)
    if interaction and not interaction.response.is_done():
        await ctx.defer(ephemeral=ephemeral)


async def safe_ctx_send(ctx, content=None, **kwargs):
    """Send without allowing an expired interaction to raise a second error."""
    try:
        return await ctx.send(content, **kwargs)
    except discord.NotFound:
        logger.warning("Discord interaction expired before a response could be sent")
        return None
    except discord.HTTPException as exc:
        logger.warning(f"Discord response failed: {exc}")
        return None


async def docker_call(func, *args, **kwargs):
    """Run blocking Docker SDK operations outside Discord's event loop."""
    return await asyncio.to_thread(func, *args, **kwargs)

async def run_docker_command(container_id, command, timeout=120):
    """Run a Docker command asynchronously with timeout"""
    try:
        process = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id, *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            if process.returncode != 0:
                raise Exception(f"Command failed: {stderr.decode()}")
            return True, stdout.decode()
        except asyncio.TimeoutError:
            process.kill()
            raise Exception(f"Command timed out after {timeout} seconds")
    except Exception as e:
        logger.error(f"Error running Docker command: {e}")
        return False, str(e)


async def generate_fresh_ssh_session(container_id):
    """Replace the current tmate process and return a brand-new SSH command."""
    socket_name = f"/tmp/titancloud-{generate_token()[:10]}.sock"
    command = (
        "pkill -f '[t]mate' >/dev/null 2>&1 || true; "
        "rm -f /tmp/titancloud-*.sock; "
        f"tmate -S {socket_name} new-session -d; "
        f"tmate -S {socket_name} wait tmate-ready; "
        f"tmate -S {socket_name} display -p '#{{tmate_ssh}}'"
    )
    success, output = await run_docker_command(
        container_id,
        ["bash", "-lc", command],
        timeout=45
    )
    if not success:
        raise RuntimeError(output)
    session = output.strip().splitlines()[-1] if output.strip() else ''
    if not session.startswith('ssh '):
        raise RuntimeError("tmate did not return a valid SSH session")
    return session

async def kill_apt_processes(container_id):
    """Kill any running apt processes"""
    try:
        success, _ = await run_docker_command(container_id, ["bash", "-c", "killall apt apt-get dpkg || true"])
        await asyncio.sleep(2)
        success, _ = await run_docker_command(container_id, ["bash", "-c", "rm -f /var/lib/apt/lists/lock /var/cache/apt/archives/lock /var/lib/dpkg/lock*"])
        await asyncio.sleep(2)
        return success
    except Exception as e:
        logger.error(f"Error killing apt processes: {e}")
        return False

async def wait_for_apt_lock(container_id, status_msg):
    """Wait for apt lock to be released"""
    max_attempts = 5
    for attempt in range(max_attempts):
        try:
            await kill_apt_processes(container_id)

            process = await asyncio.create_subprocess_exec(
                "docker", "exec", container_id, "bash", "-c", "lsof /var/lib/dpkg/lock-frontend",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                return True

            if isinstance(status_msg, discord.Interaction):
                await status_msg.followup.send(f"<a:48084loadingcircle:1536290034930548786> Waiting for package manager to be ready... (Attempt {attempt + 1}/{max_attempts})", ephemeral=True)
            else:
                await status_msg.edit(content=f"<a:48084loadingcircle:1536290034930548786> Waiting for package manager to be ready... (Attempt {attempt + 1}/{max_attempts})")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Error checking apt lock: {e}")
            await asyncio.sleep(5)

    return False

async def build_custom_image(vps_id, username, root_password, user_password, base_image=DEFAULT_OS_IMAGE):
    """Build a custom Docker image using our template"""
    try:
        # Create a temporary directory for the Dockerfile
        temp_dir = f"temp_dockerfiles/{vps_id}"
        os.makedirs(temp_dir, exist_ok=True)

        # Generate Dockerfile content
        dockerfile_content = DOCKERFILE_TEMPLATE.format(
            base_image=base_image,
            root_password=root_password,
            username=username,
            user_password=user_password,
            welcome_message=WELCOME_MESSAGE,
            watermark=WATERMARK,
            vps_id=vps_id
        )

        # Write Dockerfile
        dockerfile_path = os.path.join(temp_dir, "Dockerfile")
        with open(dockerfile_path, 'w') as f:
            f.write(dockerfile_content)

        # Build the image
        image_tag = f"titancloud/{vps_id.lower()}:latest"
        build_process = await asyncio.create_subprocess_exec(
            "docker", "build", "-t", image_tag, temp_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await build_process.communicate()

        if build_process.returncode != 0:
            raise Exception(f"Failed to build image: {stderr.decode()}")

        return image_tag
    except Exception as e:
        logger.error(f"Error building custom image: {e}")
        raise
    finally:
        # Clean up temporary directory
        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
        except Exception as e:
            logger.error(f"Error cleaning up temp directory: {e}")

async def setup_container(container_id, status_msg, memory, username, vps_id=None, use_custom_image=False):
    """Enhanced container setup with TitanCloud customization"""
    try:
        # Ensure container is running
        if isinstance(status_msg, discord.Interaction):
            await status_msg.followup.send("<:30208managementhexagon:1536290031398817824> Checking container status...", ephemeral=True)
        else:
            await status_msg.edit(content="<:30208managementhexagon:1536290031398817824> Checking container status...")

        container = await docker_call(bot.docker_client.containers.get, container_id)
        if container.status != "running":
            if isinstance(status_msg, discord.Interaction):
                await status_msg.followup.send("<:30208managementhexagon:1536290031398817824> Starting container...", ephemeral=True)
            else:
                await status_msg.edit(content="<:30208managementhexagon:1536290031398817824> Starting container...")
            await docker_call(container.start)
            await asyncio.sleep(5)

        # Generate SSH password
        ssh_password = generate_ssh_password()

        # Install tmate and other required packages
        if not use_custom_image:
            if isinstance(status_msg, discord.Interaction):
                await status_msg.followup.send("<:30208managementhexagon:1536290031398817824> Installing required packages...", ephemeral=True)
            else:
                await status_msg.edit(content="<:30208managementhexagon:1536290031398817824> Installing required packages...")

            # Update package list
            success, output = await run_docker_command(container_id, ["apt-get", "update"])
            if not success:
                raise Exception(f"Failed to update package list: {output}")

            # Install packages
            packages = [
                "tmate", "neofetch", "screen", "wget", "curl", "htop", "nano", "vim",
                "openssh-server", "sudo", "ufw", "git", "docker.io", "systemd", "systemd-sysv"
            ]
            success, output = await run_docker_command(container_id, ["apt-get", "install", "-y"] + packages)
            if not success:
                raise Exception(f"Failed to install packages: {output}")

        # Setup SSH
        if isinstance(status_msg, discord.Interaction):
            await status_msg.followup.send("<:30208managementhexagon:1536290031398817824> Configuring SSH access...", ephemeral=True)
        else:
            await status_msg.edit(content="<:30208managementhexagon:1536290031398817824> Configuring SSH access...")

        # Create user and set password (if not using custom image)
        if not use_custom_image:
            user_setup_commands = [
                f"useradd -m -s /bin/bash {username}",
                f"echo '{username}:{ssh_password}' | chpasswd",
                f"usermod -aG sudo {username}",
                "sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin no/' /etc/ssh/sshd_config",
                "sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/' /etc/ssh/sshd_config",
                "service ssh restart"
            ]

            for cmd in user_setup_commands:
                success, output = await run_docker_command(container_id, ["bash", "-c", cmd])
                if not success:
                    raise Exception(f"Failed to setup user: {output}")

        # Set TitanCloud customization
        if isinstance(status_msg, discord.Interaction):
            await status_msg.followup.send("<:30208managementhexagon:1536290031398817824> Setting up TitanCloud customization...", ephemeral=True)
        else:
            await status_msg.edit(content="<:30208managementhexagon:1536290031398817824> Setting up TitanCloud customization...")

        # Create welcome message file
        welcome_cmd = f"echo '{WELCOME_MESSAGE}' > /etc/motd && echo 'echo \"{WELCOME_MESSAGE}\"' >> /home/{username}/.bashrc"
        success, output = await run_docker_command(container_id, ["bash", "-c", welcome_cmd])
        if not success:
            logger.warning(f"Could not set welcome message: {output}")

        # Set hostname and watermark
        if not vps_id:
            vps_id = generate_vps_id()
        hostname_cmd = f"echo 'titancloud-{vps_id}' > /etc/hostname && hostname titancloud-{vps_id}"
        success, output = await run_docker_command(container_id, ["bash", "-c", hostname_cmd])
        if not success:
            raise Exception(f"Failed to set hostname: {output}")

        # Docker's host-side mem_limit is the authoritative cgroup setting.
        # Containers cannot write their own cgroup limits on a properly secured
        # host, so do not attempt to echo into /sys/fs/cgroup/memory.max.
        if isinstance(status_msg, discord.Interaction):
            await status_msg.followup.send(f"{EMOJI_LOADING} Verifying resource limits...", ephemeral=True)
        else:
            await status_msg.edit(content=f"{EMOJI_LOADING} Verifying resource limits...")

        # Set watermark in machine info
        success, output = await run_docker_command(container_id, ["bash", "-c", f"echo '{WATERMARK}' > /etc/machine-info"])
        if not success:
            logger.warning(f"Could not set machine info: {output}")

        # Basic security setup
        security_commands = [
            "if command -v ufw >/dev/null 2>&1; then ufw allow ssh && ufw --force enable; fi",
            "apt-get -y autoremove",
            "apt-get clean",
            f"chown -R {username}:{username} /home/{username}",
            f"chmod 700 /home/{username}"
        ]

        for cmd in security_commands:
            success, output = await run_docker_command(container_id, ["bash", "-c", cmd])
            if not success:
                logger.warning(f"Security setup command failed: {cmd} - {output}")

        if isinstance(status_msg, discord.Interaction):
            await status_msg.followup.send("<a:8349verify:1536290099526901850> TitanCloud VPS setup completed successfully!", ephemeral=True)
        else:
            await status_msg.edit(content="<a:8349verify:1536290099526901850> TitanCloud VPS setup completed successfully!")

        return True, ssh_password, vps_id
    except Exception as e:
        error_msg = f"Setup failed: {str(e)}"
        logger.error(error_msg)
        if isinstance(status_msg, discord.Interaction):
            await status_msg.followup.send(f"<a:8349verify:1536290099526901850> {error_msg}", ephemeral=True)
        else:
            await status_msg.edit(content=f"<a:8349verify:1536290099526901850> {error_msg}")
        return False, None, None

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = TitanCloudBot(command_prefix='/', intents=intents, help_command=None)

@bot.event
async def on_ready():
    logger.info(f'{bot.user} has connected to Discord!')

    if bot.ready_once:
        return

    # Auto-start VPS containers based on status
    if bot.docker_client:
        for token, vps in bot.db.get_all_vps().items():
            if vps['status'] == 'running':
                try:
                    container = await docker_call(bot.docker_client.containers.get, vps["container_id"])
                    if container.status != "running":
                        await docker_call(container.start)
                        logger.info(f"Started container for VPS {vps['vps_id']}")
                except docker.errors.NotFound:
                    logger.warning(f"Container {vps['container_id']} not found")
                except Exception as e:
                    logger.error(f"Error starting container: {e}")

    try:
        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="TitanCloud VPS control panel"))
        for guild in bot.guilds:
            try:
                member = guild.me
                if member and member.display_name != BRAND_NAME:
                    await member.edit(nick=BRAND_NAME, reason="TitanCloud branding")
            except discord.HTTPException as exc:
                logger.warning(f"Could not update nickname in guild {guild.id}: {exc}")
        synced_commands = await bot.tree.sync()
        logger.info(f"Synced {len(synced_commands)} slash commands")
        bot.ready_once = True
    except Exception as e:
        logger.error(f"Error syncing slash commands: {e}")

@bot.hybrid_command(name='help', description='Show all available commands')
async def show_commands(ctx):
    """Show a clean onboarding guide for new and existing users."""
    try:
        embed = premium_embed(
            title="WELCOME TO TITANCLOUD",
            description=(
                "Your private Linux workspace is controlled entirely from Discord. "
                "Follow the three steps below to get online."
            ),
            color=BRAND_COLOR
        )
        embed.add_field(
            name=f"{EMOJI_MANAGE}  01  /  ELIGIBILITY",
            value=(
                f"You need the <@&{USER_ROLE_ID}> role. Regular members receive one VPS; "
                "configured owners may provision additional instances."
            ),
            inline=False
        )
        embed.add_field(
            name=f"{EMOJI_LOADING}  02  /  DEPLOY",
            value="Run `/deploy`. Provisioning progress appears in Discord and private credentials arrive by DM.",
            inline=False
        )
        embed.add_field(
            name=f"{EMOJI_START}  03  /  CONTROL & CONNECT",
            value=(
                "Run `/manage` to open the control panel. Use **Fresh SSH** to generate a new "
                "tmate session; every click closes the previous session and sends the replacement by DM."
            ),
            inline=False
        )
        embed.add_field(
            name="YOUR PLAN",
            value="`32 GB RAM`  •  `6 vCPU`  •  `80 GB storage`  •  `1 VPS per regular user`",
            inline=False
        )
        embed.add_field(
            name="ESSENTIAL COMMANDS",
            value=(
                "`/deploy` — create your VPS\n"
                "`/manage [vps_id]` — open Start, Stop, Restart, Reinstall, Fresh SSH and Transfer controls\n"
                "`/list` — view your VPS IDs\n"
                "`/vps_stats <vps_id>` — view live usage\n"
                "`/vps_usage` — view your allocation\n"
                "`/help` — show this guide"
            ),
            inline=False
        )

        if has_admin_role(ctx):
            embed.add_field(
                name="OWNER TOOLKIT",
                value=(
                    "`/deploy target:@member`  •  `/vps_list`  •  `/delete_vps`  •  `/admin_stats`\n"
                    "`/suspend_vps`  •  `/unsuspend_vps`  •  `/ban_user`  •  `/unban_user`\n"
                    "`/backup_data`  •  `/restore_data`  •  `/global_stats`  •  `/system_info`"
                ),
                inline=False
            )

        await safe_ctx_send(ctx, embed=embed)
        try:
            await ctx.author.send(embed=embed)
        except discord.Forbidden:
            pass
    except Exception as e:
        logger.error(f"Error in show_commands: {e}")
        await safe_ctx_send(ctx, f"{SYMBOL['error']} Could not open the help guide.", ephemeral=True)

@bot.hybrid_command(name='add_admin', description='Add a new admin (Admin only)')
@app_commands.describe(
    user="User to make admin"
)
async def add_admin(ctx, user: discord.User):
    """Add a new admin user"""
    if not has_admin_role(ctx):
        await ctx.send("<a:8349verify:1536290099526901850> You must be an admin to use this command!", ephemeral=True)
        return

    bot.db.add_admin(user.id)
    await ctx.send(f"<a:8349verify:1536290099526901850> {user.mention} has been added as an admin!", ephemeral=True)

@bot.hybrid_command(name='remove_admin', description='Remove an admin (Owner only)')
@app_commands.describe(
    user="User to remove from admin"
)
async def remove_admin(ctx, user: discord.User):
    """Remove an admin user (Owner only)"""
    if not is_owner(ctx):
        await ctx.send("<a:8349verify:1536290099526901850> Only the owner can remove admins!", ephemeral=True)
        return

    bot.db.remove_admin(user.id)
    await ctx.send(f"<a:8349verify:1536290099526901850> {user.mention} has been removed from admins!", ephemeral=True)

@bot.hybrid_command(name='list_admins', description='List all admin users')
async def list_admins(ctx):
    """List all admin users"""
    if not has_admin_role(ctx):
        await ctx.send("<a:8349verify:1536290099526901850> You must be an admin to use this command!", ephemeral=True)
        return

    embed = premium_embed(title="Admin Users", color=discord.Color.blue())

    # List user IDs in ADMIN_IDS
    admin_list = []
    for admin_id in ADMIN_IDS:
        try:
            user = await bot.fetch_user(admin_id)
            admin_list.append(f"{user.name} ({user.id})")
        except:
            admin_list.append(f"Unknown User ({admin_id})")

    # List users with admin role
    if ctx.guild:
        admin_role = ctx.guild.get_role(OWNER_ROLE_ID)
        if admin_role:
            role_admins = [f"{member.name} ({member.id})" for member in admin_role.members]
            admin_list.extend(role_admins)

    if not admin_list:
        embed.description = "No admins found"
    else:
        embed.description = "\n".join(sorted(set(admin_list)))  # Remove duplicates

    await ctx.send(embed=embed, ephemeral=True)

@bot.hybrid_command(name='deploy', description='Deploy a premium 32 GB VPS')
@app_commands.describe(
    target="Owner only: member who will receive the VPS",
    os_image="OS image to use",
    use_custom_image="Use the customized TitanCloud image"
)
async def deploy_command(
    ctx,
    target: Optional[discord.Member] = None,
    os_image: str = DEFAULT_OS_IMAGE,
    use_custom_image: bool = True
):
    """Deploy a fixed-plan VPS, with unlimited owner provisioning."""
    await defer_context(ctx)

    if not ctx.guild:
        await ctx.send(f"{SYMBOL['error']} This command can only be used in a server.", ephemeral=True)
        return

    owner_request = is_owner(ctx)
    if target is not None and not owner_request and target.id != ctx.author.id:
        await ctx.send(
            f"{SYMBOL['error']} Only an owner can deploy for another member.",
            ephemeral=True
        )
        return

    if not owner_request and not has_user_role(ctx):
        await ctx.send(
            f"{SYMBOL['error']} You need the <@&{USER_ROLE_ID}> role to deploy a VPS.",
            ephemeral=True
        )
        return

    owner = target or ctx.author
    memory = VPS_MEMORY_GB
    cpu = DEFAULT_CPU_CORES
    disk = DEFAULT_DISK_GB

    if bot.db.is_user_banned(owner.id):
        await ctx.send(f"{SYMBOL['error']} Your VPS access is suspended.", ephemeral=True)
        return

    if not bot.docker_client:
        await ctx.send(f"{SYMBOL['error']} Provisioning is temporarily unavailable. Contact an owner.", ephemeral=True)
        return

    registered_request = False
    if not owner_request:
        async with bot.provision_guard:
            if owner.id in bot.provisioning_users:
                await safe_ctx_send(
                    ctx,
                    f"{EMOJI_LOADING} Your VPS is already being provisioned.",
                    ephemeral=True
                )
                return
            bot.provisioning_users.add(owner.id)
            registered_request = True

    try:
        await asyncio.wait_for(bot.provision_semaphore.acquire(), timeout=10)
    except asyncio.TimeoutError:
        if registered_request:
            async with bot.provision_guard:
                bot.provisioning_users.discard(owner.id)
        await safe_ctx_send(
            ctx,
            f"{EMOJI_LOADING} Provisioning capacity is busy. Please retry in a few minutes.",
            ephemeral=True
        )
        return
    provisioned = False
    try:
        # Check if we've reached container limit
        containers = await docker_call(bot.docker_client.containers.list, all=True)
        if len(containers) >= bot.db.get_setting('max_containers', MAX_CONTAINERS):
            await ctx.send(f"{SYMBOL['error']} Capacity is full. Contact an owner.", ephemeral=True)
            return

        # Owners may provision any number of containers for themselves or any
        # target member, up to the configured global host capacity.
        if not owner_request and bot.db.get_user_vps_count(owner.id) >= MAX_VPS_PER_USER:
            await ctx.send(f"{SYMBOL['info']} You already have a container. Use `/list` or `/manage`.", ephemeral=True)
            return

        status_msg = await ctx.send(
            f"{SYMBOL['progress']} Reserving a 32 GB container for {owner.mention}..."
        )

        memory_bytes = memory * 1024 * 1024 * 1024
        vps_id = generate_vps_id()
        username = owner.name.lower().replace(" ", "_")[:20]
        root_password = generate_ssh_password()
        user_password = generate_ssh_password()
        token = generate_token()

        if use_custom_image:
            await status_msg.edit(content=f"{SYMBOL['progress']} Building your private system image...")
            try:
                image_tag = await build_custom_image(vps_id, username, root_password, user_password, os_image)
            except Exception as e:
                await status_msg.edit(content=f"{SYMBOL['error']} Image build failed: {str(e)}")
                return

            await status_msg.edit(content=f"{SYMBOL['progress']} Initializing the container...")
            try:
                container = await docker_call(
                    bot.docker_client.containers.run,
                    image_tag,
                    detach=True,
                    privileged=True,
                    hostname=f"titancloud-{vps_id}",
                    mem_limit=memory_bytes,
                    cpu_period=100000,
                    cpu_quota=int(cpu * 100000),
                    cap_add=["ALL"],
                    network=DOCKER_NETWORK,
                    volumes={
                        f'titancloud-{vps_id}': {'bind': '/data', 'mode': 'rw'}
                    },
                    restart_policy={"Name": "always"}
                )
            except Exception as e:
                await status_msg.edit(content=f"{SYMBOL['error']} Container startup failed: {str(e)}")
                return
        else:
            await status_msg.edit(content=f"{SYMBOL['progress']} Initializing the container...")
            try:
                container = await docker_call(
                    bot.docker_client.containers.run,
                    os_image,
                    detach=True,
                    privileged=True,
                    hostname=f"titancloud-{vps_id}",
                    mem_limit=memory_bytes,
                    cpu_period=100000,
                    cpu_quota=int(cpu * 100000),
                    cap_add=["ALL"],
                    command="tail -f /dev/null",
                    tty=True,
                    network=DOCKER_NETWORK,
                    volumes={
                        f'titancloud-{vps_id}': {'bind': '/data', 'mode': 'rw'}
                    },
                    restart_policy={"Name": "always"}
                )
            except docker.errors.ImageNotFound:
                await status_msg.edit(content=f"{SYMBOL['info']} Image unavailable; using {DEFAULT_OS_IMAGE}.")
                container = await docker_call(
                    bot.docker_client.containers.run,
                    DEFAULT_OS_IMAGE,
                    detach=True,
                    privileged=True,
                    hostname=f"titancloud-{vps_id}",
                    mem_limit=memory_bytes,
                    cpu_period=100000,
                    cpu_quota=int(cpu * 100000),
                    cap_add=["ALL"],
                    command="tail -f /dev/null",
                    tty=True,
                    network=DOCKER_NETWORK,
                    volumes={
                        f'titancloud-{vps_id}': {'bind': '/data', 'mode': 'rw'}
                    },
                    restart_policy={"Name": "always"}
                )
                os_image = DEFAULT_OS_IMAGE

        await status_msg.edit(content=f"{SYMBOL['progress']} Applying the TitanCloud environment...")
        await asyncio.sleep(5)

        setup_success, ssh_password, _ = await setup_container(
            container.id,
            status_msg,
            memory,
            username,
            vps_id,
            use_custom_image=use_custom_image
        )
        if not setup_success:
            raise Exception("Failed to setup container")

        await status_msg.edit(content=f"{SYMBOL['progress']} Securing the remote access session...")

        ssh_session_line = await generate_fresh_ssh_session(container.id)

        vps_data = {
            "token": token,
            "vps_id": vps_id,
            "container_id": container.id,
            "memory": memory,
            "cpu": cpu,
            "disk": disk,
            "username": username,
            "password": ssh_password,
            "root_password": root_password if use_custom_image else None,
            "created_by": str(owner.id),
            "created_at": str(datetime.datetime.now()),
            "tmate_session": ssh_session_line,
            "watermark": WATERMARK,
            "os_image": os_image,
            "restart_count": 0,
            "last_restart": None,
            "status": "running",
            "use_custom_image": use_custom_image
        }

        bot.db.add_vps(vps_data)
        provisioned = True

        try:
            embed = premium_embed("DEPLOYMENT COMPLETE", "Your private VPS is online.", SUCCESS_COLOR)
            embed.add_field(name="VPS ID", value=f"`{vps_id}`", inline=True)
            embed.add_field(name="MEMORY", value=f"`{memory} GB`", inline=True)
            embed.add_field(name="COMPUTE", value=f"`{cpu} vCPU`", inline=True)
            embed.add_field(name="STORAGE", value=f"`{disk} GB`", inline=True)
            embed.add_field(name="USERNAME", value=f"`{username}`", inline=True)
            embed.add_field(name="USER PASSWORD", value=f"||{ssh_password}||", inline=False)
            if use_custom_image:
                embed.add_field(name="ROOT PASSWORD", value=f"||{root_password}||", inline=False)
            embed.add_field(name="SECURE SESSION", value=f"```{ssh_session_line}```", inline=False)
            embed.add_field(name="PLAN", value="`32 GB RAM`  /  `1 container maximum`", inline=False)

            await owner.send(embed=embed)
            await status_msg.edit(content=f"{SYMBOL['success']} Deployment complete, {owner.mention}. Access details were sent by DM.")
        except discord.Forbidden:
            try:
                await status_msg.edit(content=f"{SYMBOL['info']} Container created, but your DMs are closed. Enable server DMs and contact an owner.")
            except discord.HTTPException:
                pass
        except discord.HTTPException as exc:
            # Provisioning has already committed. A failed notification must
            # never delete a successfully created VPS.
            logger.warning(f"VPS created but Discord notification failed: {exc}")

    except Exception as e:
        error_msg = f"{SYMBOL['error']} Provisioning failed: {str(e)}"
        logger.error(error_msg)
        await safe_ctx_send(ctx, error_msg, ephemeral=True)
        if not provisioned and 'container' in locals():
            try:
                await docker_call(container.stop)
                await docker_call(container.remove)
            except Exception as e:
                logger.error(f"Error cleaning up container: {e}")
    finally:
        bot.provision_semaphore.release()
        if registered_request:
            async with bot.provision_guard:
                bot.provisioning_users.discard(owner.id)

@bot.hybrid_command(name='list', description='List all your VPS instances')
async def list_vps(ctx):
    """List all VPS instances owned by the user"""
    try:
        user_vps = bot.db.get_user_vps(ctx.author.id)

        if not user_vps:
            await ctx.send("You don't have any VPS instances.", ephemeral=True)
            return

        embed = premium_embed(title="Your TitanCloud VPS Instances", color=discord.Color.blue())

        for vps in user_vps:
            try:
                # Handle missing container ID gracefully
                container = bot.docker_client.containers.get(vps["container_id"]) if vps["container_id"] else None
                status = vps['status'].capitalize() if vps.get('status') else "Unknown"
            except Exception as e:
                status = "Not Found"
                logger.error(f"Error fetching container {vps['container_id']}: {e}")

            # Adding fields safely to prevent missing keys causing errors
            embed.add_field(
                name=f"VPS {vps['vps_id']}",
                value=f"""
Status: {status}
Memory: {vps.get('memory', 'Unknown')}GB
CPU: {vps.get('cpu', 'Unknown')} cores
Disk Allocated: {vps.get('disk', 'Unknown')}GB
Username: {vps.get('username', 'Unknown')}
OS: {vps.get('os_image', DEFAULT_OS_IMAGE)}
Created: {vps.get('created_at', 'Unknown')}
Restarts: {vps.get('restart_count', 0)}
""",
                inline=False
            )

        await ctx.send(embed=embed)
    except Exception as e:
        logger.error(f"Error in list_vps: {e}")
        await ctx.send(f"<a:8349verify:1536290099526901850> Error listing VPS instances: {str(e)}")

@bot.hybrid_command(name='vps_list', description='List all VPS instances (Admin only)')
async def admin_list_vps(ctx):
    """List all VPS instances (Admin only)"""
    if not has_admin_role(ctx):
        await ctx.send("<a:8349verify:1536290099526901850> You must be an admin to use this command!", ephemeral=True)
        return

    try:
        all_vps = bot.db.get_all_vps()
        if not all_vps:
            await ctx.send("No VPS instances found.", ephemeral=True)
            return

        embed = premium_embed(title="All TitanCloud VPS Instances", color=discord.Color.blue())
        valid_vps_count = 0

        for token, vps in all_vps.items():
            try:
                # Fetch username of the owner with error handling
                user = await bot.fetch_user(int(vps.get("created_by", "0")))
                username = user.name if user else "Unknown User"
            except Exception as e:
                username = "Unknown User"
                logger.error(f"Error fetching user {vps.get('created_by')}: {e}")

            try:
                # Handle missing container ID gracefully
                container = bot.docker_client.containers.get(vps.get("container_id", "")) if vps.get("container_id") else None
                container_status = container.status if container else "Not Found"
            except Exception as e:
                container_status = "Not Found"
                logger.error(f"Error fetching container {vps.get('container_id')}: {e}")

            # Get status and other info with error fallback
            status = vps.get('status', "Unknown").capitalize()

            vps_info = f"""
Owner: {username}
Status: {status} (Container: {container_status})
Memory: {vps.get('memory', 'Unknown')}GB
CPU: {vps.get('cpu', 'Unknown')} cores
Disk: {vps.get('disk', 'Unknown')}GB
Username: {vps.get('username', 'Unknown')}
OS: {vps.get('os_image', DEFAULT_OS_IMAGE)}
Created: {vps.get('created_at', 'Unknown')}
Restarts: {vps.get('restart_count', 0)}
"""

            embed.add_field(
                name=f"VPS {vps.get('vps_id', 'Unknown')}",
                value=vps_info,
                inline=False
            )
            valid_vps_count += 1

        if valid_vps_count == 0:
            await ctx.send("No valid VPS instances found.", ephemeral=True)
            return

        embed.set_footer(text=f"Total VPS instances: {valid_vps_count}")
        await ctx.send(embed=embed)
    except Exception as e:
        logger.error(f"Error in admin_list_vps: {e}")
        await ctx.send(f"<a:8349verify:1536290099526901850> Error listing VPS instances: {str(e)}")

@bot.hybrid_command(name='delete_vps', description='Delete a VPS instance (Admin only)')
@app_commands.describe(
    vps_id="ID of the VPS to delete"
)
async def delete_vps(ctx, vps_id: str):
    """Delete a VPS instance (Admin only)"""
    if not has_admin_role(ctx):
        await ctx.send("<a:8349verify:1536290099526901850> You must be an admin to use this command!", ephemeral=True)
        return

    try:
        token, vps = bot.db.get_vps_by_id(vps_id)
        if not vps:
            await ctx.send("<a:8349verify:1536290099526901850> VPS not found!", ephemeral=True)
            return

        try:
            container = await docker_call(bot.docker_client.containers.get, vps["container_id"])
            container.stop()
            container.remove()
            logger.info(f"Deleted container {vps['container_id']} for VPS {vps_id}")
        except Exception as e:
            logger.error(f"Error removing container: {e}")

        bot.db.remove_vps(token)

        await ctx.send(f"<a:8349verify:1536290099526901850> TitanCloud VPS {vps_id} has been deleted successfully!")
    except Exception as e:
        logger.error(f"Error in delete_vps: {e}")
        await ctx.send(f"<a:8349verify:1536290099526901850> Error deleting VPS: {str(e)}")

@bot.hybrid_command(name='connect_vps', description='Connect to a VPS using the provided token')
@app_commands.describe(
    token="Access token for the VPS"
)
async def connect_vps(ctx, token: str):
    """Connect to a VPS using the provided token"""
    await defer_context(ctx, ephemeral=True)
    vps = bot.db.get_vps_by_token(token)
    if not vps:
        await ctx.send("<a:8349verify:1536290099526901850> Invalid token!", ephemeral=True)
        return

    if str(ctx.author.id) != vps["created_by"] and not has_admin_role(ctx):
        await ctx.send("<a:8349verify:1536290099526901850> You don't have permission to access this VPS!", ephemeral=True)
        return

    try:
        try:
            container = await docker_call(bot.docker_client.containers.get, vps["container_id"])
            if container.status != "running":
                await docker_call(container.start)
                await asyncio.sleep(5)
        except:
            await ctx.send("<a:8349verify:1536290099526901850> VPS instance not found or is no longer available.", ephemeral=True)
            return

        ssh_session_line = await generate_fresh_ssh_session(vps["container_id"])

        bot.db.update_vps(token, {"tmate_session": ssh_session_line})

        embed = premium_embed(title="TitanCloud VPS Connection Details", color=discord.Color.blue())
        embed.add_field(name="Username", value=vps["username"], inline=True)
        embed.add_field(name="SSH Password", value=f"||{vps.get('password', 'Not set')}||", inline=True)
        embed.add_field(name="Tmate Session", value=f"```{ssh_session_line}```", inline=False)
        embed.add_field(
            name="HOW TO CONNECT",
            value=(
                "1. Copy the private tmate command above.\n"
                "2. Open a terminal on your computer.\n"
                "3. Paste the command and press Enter.\n"
                "4. Use **Fresh SSH** in `/manage` whenever you need a replacement session."
            ),
            inline=False
        )

        await ctx.author.send(embed=embed)
        await ctx.send("<a:8349verify:1536290099526901850> Connection details sent to your DMs! Use the Tmate command to connect to your TitanCloud VPS.", ephemeral=True)

    except discord.Forbidden:
        await ctx.send("<a:8349verify:1536290099526901850> I couldn't send you a DM. Please enable DMs from server members.", ephemeral=True)
    except Exception as e:
        logger.error(f"Error in connect_vps: {e}")
        await ctx.send(f"<a:8349verify:1536290099526901850> An error occurred while connecting to the VPS: {str(e)}", ephemeral=True)

@bot.hybrid_command(name='vps_stats', description='Show resource usage for a VPS')
@app_commands.describe(
    vps_id="ID of the VPS to check"
)
async def vps_stats(ctx, vps_id: str):
    """Show resource usage for a VPS"""
    try:
        token, vps = bot.db.get_vps_by_id(vps_id)
        if not vps or (vps["created_by"] != str(ctx.author.id) and not has_admin_role(ctx)):
            await ctx.send("<a:8349verify:1536290099526901850> VPS not found or you don't have access to it!", ephemeral=True)
            return

        try:
            container = bot.docker_client.containers.get(vps["container_id"])
            if container.status != "running":
                await ctx.send("<a:8349verify:1536290099526901850> VPS is not running!", ephemeral=True)
                return

            # Get memory stats
            mem_process = await asyncio.create_subprocess_exec(
                "docker", "exec", vps["container_id"], "free", "-m",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await mem_process.communicate()

            if mem_process.returncode != 0:
                raise Exception(f"Failed to get memory info: {stderr.decode()}")

            # Get CPU stats
            cpu_process = await asyncio.create_subprocess_exec(
                "docker", "exec", vps["container_id"], "top", "-bn1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            cpu_stdout, cpu_stderr = await cpu_process.communicate()

            # Get disk stats
            disk_process = await asyncio.create_subprocess_exec(
                "docker", "exec", vps["container_id"], "df", "-h",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            disk_stdout, disk_stderr = await disk_process.communicate()

            embed = premium_embed(title=f"Resource Usage for VPS {vps_id}", color=discord.Color.blue())
            embed.add_field(name="Memory Info", value=f"```{stdout.decode()}```", inline=False)

            if disk_process.returncode == 0:
                embed.add_field(name="Disk Info", value=f"```{disk_stdout.decode()}```", inline=False)

            embed.add_field(name="Configured Limits", value=f"""
Memory: {vps['memory']}GB
CPU: {vps['cpu']} cores
Disk Allocated: {vps['disk']}GB
""", inline=True)

            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"<a:8349verify:1536290099526901850> Error checking VPS stats: {str(e)}", ephemeral=True)
    except Exception as e:
        logger.error(f"Error in vps_stats: {e}")
        await ctx.send(f"<a:8349verify:1536290099526901850> Error: {str(e)}", ephemeral=True)

@bot.hybrid_command(name='change_ssh_password', description='Change the SSH password for a VPS')
@app_commands.describe(
    vps_id="ID of the VPS to update"
)
async def change_ssh_password(ctx, vps_id: str):
    """Change the SSH password for a VPS"""
    try:
        token, vps = bot.db.get_vps_by_id(vps_id)
        if not vps or vps["created_by"] != str(ctx.author.id):
            await ctx.send("<a:8349verify:1536290099526901850> VPS not found or you don't have access to it!", ephemeral=True)
            return

        try:
            container = bot.docker_client.containers.get(vps["container_id"])
            if container.status != "running":
                await ctx.send("<a:8349verify:1536290099526901850> VPS is not running!", ephemeral=True)
                return

            new_password = generate_ssh_password()

            process = await asyncio.create_subprocess_exec(
                "docker", "exec", vps["container_id"], "bash", "-c", f"echo '{vps['username']}:{new_password}' | chpasswd",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                raise Exception(f"Failed to change password: {stderr.decode()}")

            bot.db.update_vps(token, {'password': new_password})

            embed = premium_embed(title=f"SSH Password Updated for VPS {vps_id}", color=discord.Color.green())
            embed.add_field(name="Username", value=vps['username'], inline=True)
            embed.add_field(name="New Password", value=f"||{new_password}||", inline=False)

            await ctx.author.send(embed=embed)
            await ctx.send("<a:8349verify:1536290099526901850> SSH password updated successfully! Check your DMs for the new password.", ephemeral=True)
        except Exception as e:
            await ctx.send(f"<a:8349verify:1536290099526901850> Error changing SSH password: {str(e)}", ephemeral=True)
    except Exception as e:
        logger.error(f"Error in change_ssh_password: {e}")
        await ctx.send(f"<a:8349verify:1536290099526901850> Error: {str(e)}", ephemeral=True)

@bot.hybrid_command(name='admin_stats', description='Show system statistics (Admin only)')
async def admin_stats(ctx):
    """Show system statistics (Admin only)"""
    if not has_admin_role(ctx):
        await ctx.send("<a:8349verify:1536290099526901850> You must be an admin to use this command!", ephemeral=True)
        return

    try:
        # Get Docker stats
        containers = bot.docker_client.containers.list(all=True) if bot.docker_client else []

        # Get system stats
        stats = bot.system_stats

        embed = premium_embed(title="TitanCloud System Statistics", color=discord.Color.blue())
        embed.add_field(name="VPS Instances", value=f"Total: {len(bot.db.get_all_vps())}\nRunning: {len([c for c in containers if c.status == 'running'])}", inline=True)
        embed.add_field(name="Docker Containers", value=f"Total: {len(containers)}\nRunning: {len([c for c in containers if c.status == 'running'])}", inline=True)
        embed.add_field(name="CPU Usage", value=f"{stats['cpu_usage']}%", inline=True)
        embed.add_field(name="Memory Usage", value=f"{stats['memory_usage']}% ({stats['memory_used']:.2f}GB / {stats['memory_total']:.2f}GB)", inline=True)
        embed.add_field(name="Disk Usage", value=f"{stats['disk_usage']}% ({stats['disk_used']:.2f}GB / {stats['disk_total']:.2f}GB)", inline=True)
        embed.add_field(name="Network", value=f"Sent: {stats['network_sent']:.2f}MB\nRecv: {stats['network_recv']:.2f}MB", inline=True)
        embed.add_field(name="Container Limit", value=f"{len(containers)}/{bot.db.get_setting('max_containers')}", inline=True)
        embed.add_field(name="Last Updated", value=f"<t:{int(stats['last_updated'])}:R>", inline=True)

        await ctx.send(embed=embed)
    except Exception as e:
        logger.error(f"Error in admin_stats: {e}")
        await ctx.send(f"<a:8349verify:1536290099526901850> Error getting system stats: {str(e)}", ephemeral=True)

@bot.hybrid_command(name='system_info', description='Show detailed system information (Admin only)')
async def system_info(ctx):
    """Show detailed system information (Admin only)"""
    if not has_admin_role(ctx):
        await ctx.send("<a:8349verify:1536290099526901850> You must be an admin to use this command!", ephemeral=True)
        return

    try:
        # System information
        uname = platform.uname()
        boot_time = datetime.datetime.fromtimestamp(psutil.boot_time())

        # CPU information
        cpu_info = f"""
System: {uname.system}
Node Name: {uname.node}
Release: {uname.release}
Version: {uname.version}
Machine: {uname.machine}
Processor: {uname.processor}
Physical cores: {psutil.cpu_count(logical=False)}
Total cores: {psutil.cpu_count(logical=True)}
CPU Usage: {psutil.cpu_percent()}%
"""

        # Memory Information
        svmem = psutil.virtual_memory()
        mem_info = f"""
Total: {svmem.total / (1024**3):.2f}GB
Available: {svmem.available / (1024**3):.2f}GB
Used: {svmem.used / (1024**3):.2f}GB
Percentage: {svmem.percent}%
"""

        # Disk Information
        partitions = psutil.disk_partitions()
        disk_info = ""
        for partition in partitions:
            try:
                partition_usage = psutil.disk_usage(partition.mountpoint)
                disk_info += f"""
Device: {partition.device}
  Mountpoint: {partition.mountpoint}
  File system type: {partition.fstype}
  Total Size: {partition_usage.total / (1024**3):.2f}GB
  Used: {partition_usage.used / (1024**3):.2f}GB
  Free: {partition_usage.free / (1024**3):.2f}GB
  Percentage: {partition_usage.percent}%
"""
            except PermissionError:
                continue

        # Network information
        net_io = psutil.net_io_counters()
        net_info = f"""
Bytes Sent: {net_io.bytes_sent / (1024**2):.2f}MB
Bytes Received: {net_io.bytes_recv / (1024**2):.2f}MB
"""

        embed = premium_embed(title="Detailed System Information", color=discord.Color.blue())
        embed.add_field(name="System", value=f"Boot Time: {boot_time}", inline=False)
        embed.add_field(name="CPU Info", value=f"```{cpu_info}```", inline=False)
        embed.add_field(name="Memory Info", value=f"```{mem_info}```", inline=False)
        embed.add_field(name="Disk Info", value=f"```{disk_info}```", inline=False)
        embed.add_field(name="Network Info", value=f"```{net_info}```", inline=False)

        await ctx.send(embed=embed)
    except Exception as e:
        logger.error(f"Error in system_info: {e}")
        await ctx.send(f"<a:8349verify:1536290099526901850> Error getting system info: {str(e)}", ephemeral=True)

@bot.hybrid_command(name='container_limit', description='Set maximum container limit (Owner only)')
@app_commands.describe(
    max_limit="New maximum container limit"
)
async def set_container_limit(ctx, max_limit: int):
    """Set maximum container limit (Owner only)"""
    if not is_owner(ctx):
        await ctx.send("<a:8349verify:1536290099526901850> Only the owner can set container limit!", ephemeral=True)
        return

    if max_limit < 1 or max_limit > 1000:
        await ctx.send("<a:8349verify:1536290099526901850> Container limit must be between 1 and 1000", ephemeral=True)
        return

    bot.db.set_setting('max_containers', max_limit)
    await ctx.send(f"<a:8349verify:1536290099526901850> Maximum container limit set to {max_limit}", ephemeral=True)

@bot.hybrid_command(name='cleanup_vps', description='Cleanup inactive VPS instances (Admin only)')
async def cleanup_vps(ctx):
    """Cleanup inactive VPS instances (Admin only)"""
    if not has_admin_role(ctx):
        await ctx.send("<a:8349verify:1536290099526901850> You must be an admin to use this command!", ephemeral=True)
        return

    try:
        cleanup_count = 0

        for token, vps in list(bot.db.get_all_vps().items()):
            try:
                container = bot.docker_client.containers.get(vps['container_id'])
                if container.status != 'running':
                    container.stop()
                    container.remove()
                    bot.db.remove_vps(token)
                    cleanup_count += 1
            except docker.errors.NotFound:
                bot.db.remove_vps(token)
                cleanup_count += 1
            except Exception as e:
                logger.error(f"Error cleaning up VPS {vps['vps_id']}: {e}")
                continue

        if cleanup_count > 0:
            await ctx.send(f"<a:8349verify:1536290099526901850> Cleaned up {cleanup_count} inactive VPS instances!")
        else:
            await ctx.send("<:30208managementhexagon:1536290031398817824> No inactive VPS instances found to clean up.")
    except Exception as e:
        logger.error(f"Error in cleanup_vps: {e}")
        await ctx.send(f"<a:8349verify:1536290099526901850> Error during cleanup: {str(e)}", ephemeral=True)

@bot.hybrid_command(name='vps_usage', description='Show your VPS usage statistics')
async def vps_usage(ctx):
    """Show your VPS usage statistics"""
    try:
        user_vps = bot.db.get_user_vps(ctx.author.id)

        total_memory = sum(vps['memory'] for vps in user_vps)
        total_cpu = sum(vps['cpu'] for vps in user_vps)
        total_disk = sum(vps['disk'] for vps in user_vps)
        total_restarts = sum(vps.get('restart_count', 0) for vps in user_vps)

        embed = premium_embed(title="Your TitanCloud VPS Usage", color=discord.Color.blue())
        embed.add_field(name="Total VPS Instances", value=len(user_vps), inline=True)
        embed.add_field(name="Total Memory Allocated", value=f"{total_memory}GB", inline=True)
        embed.add_field(name="Total CPU Cores Allocated", value=total_cpu, inline=True)
        embed.add_field(name="Total Disk Allocated", value=f"{total_disk}GB", inline=True)
        embed.add_field(name="Total Restarts", value=total_restarts, inline=True)

        await ctx.send(embed=embed)
    except Exception as e:
        logger.error(f"Error in vps_usage: {e}")
        await ctx.send(f"<a:8349verify:1536290099526901850> Error: {str(e)}", ephemeral=True)

@bot.hybrid_command(name='global_stats', description='Show global usage statistics (Admin only)')
async def global_stats(ctx):
    """Show global usage statistics (Admin only)"""
    if not has_admin_role(ctx):
        await ctx.send("<a:8349verify:1536290099526901850> You must be an admin to use this command!", ephemeral=True)
        return

    try:
        all_vps = bot.db.get_all_vps()
        total_memory = sum(vps['memory'] for vps in all_vps.values())
        total_cpu = sum(vps['cpu'] for vps in all_vps.values())
        total_disk = sum(vps['disk'] for vps in all_vps.values())
        total_restarts = sum(vps.get('restart_count', 0) for vps in all_vps.values())

        embed = premium_embed(title="TitanCloud Global Usage Statistics", color=discord.Color.blue())
        embed.add_field(name="Total VPS Created", value=bot.db.get_stat('total_vps_created'), inline=True)
        embed.add_field(name="Total Restarts", value=bot.db.get_stat('total_restarts'), inline=True)
        embed.add_field(name="Current VPS Instances", value=len(all_vps), inline=True)
        embed.add_field(name="Total Memory Allocated", value=f"{total_memory}GB", inline=True)
        embed.add_field(name="Total CPU Cores Allocated", value=total_cpu, inline=True)
        embed.add_field(name="Total Disk Allocated", value=f"{total_disk}GB", inline=True)
        embed.add_field(name="Total Restarts", value=total_restarts, inline=True)

        await ctx.send(embed=embed)
    except Exception as e:
        logger.error(f"Error in global_stats: {e}")
        await ctx.send(f"<a:8349verify:1536290099526901850> Error: {str(e)}", ephemeral=True)

@bot.hybrid_command(name='migrate_vps', description='Migrate a VPS to another host (Admin only)')
@app_commands.describe(
    vps_id="ID of the VPS to migrate"
)
async def migrate_vps(ctx, vps_id: str):
    """Migrate a VPS to another host (Admin only)"""
    if not has_admin_role(ctx):
        await ctx.send("<a:8349verify:1536290099526901850> You must be an admin to use this command!", ephemeral=True)
        return

    try:
        token, vps = bot.db.get_vps_by_id(vps_id)
        if not vps:
            await ctx.send("<a:8349verify:1536290099526901850> VPS not found!", ephemeral=True)
            return

        status_msg = await ctx.send(f"<a:48084loadingcircle:1536290034930548786> Preparing to migrate VPS {vps_id}...")

        # Create a snapshot
        backup_id = generate_vps_id()[:8]
        backup_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        backup_dir = f"migrations/{vps_id}"
        os.makedirs(backup_dir, exist_ok=True)
        backup_file = f"{backup_dir}/{backup_id}.tar"

        await status_msg.edit(content=f"<a:48084loadingcircle:1536290034930548786> Creating snapshot {backup_id} for migration...")

        process = await asyncio.create_subprocess_exec(
            "docker", "export", "-o", backup_file, vps["container_id"],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise Exception(f"Snapshot failed: {stderr.decode()}")

        await status_msg.edit(content=f"<a:8349verify:1536290099526901850> Snapshot {backup_id} created successfully. Please download this file and import it on the new host: {backup_file}")

    except Exception as e:
        logger.error(f"Error in migrate_vps: {e}")
        await ctx.send(f"<a:8349verify:1536290099526901850> Error during migration: {str(e)}", ephemeral=True)

@bot.hybrid_command(name='emergency_stop', description='Force stop a problematic VPS (Admin only)')
@app_commands.describe(
    vps_id="ID of the VPS to stop"
)
async def emergency_stop(ctx, vps_id: str):
    """Force stop a problematic VPS (Admin only)"""
    if not has_admin_role(ctx):
        await ctx.send("<a:8349verify:1536290099526901850> You must be an admin to use this command!", ephemeral=True)
        return

    try:
        token, vps = bot.db.get_vps_by_id(vps_id)
        if not vps:
            await ctx.send("<a:8349verify:1536290099526901850> VPS not found!", ephemeral=True)
            return

        try:
            container = bot.docker_client.containers.get(vps["container_id"])
            if container.status != "running":
                await ctx.send("VPS is already stopped!", ephemeral=True)
                return

            await ctx.send("[!] Attempting to force stop the VPS... This may take a moment.", ephemeral=True)

            # Try normal stop first
            try:
                container.stop(timeout=10)
                bot.db.update_vps(token, {'status': 'stopped'})
                await ctx.send("<a:8349verify:1536290099526901850> VPS stopped successfully!", ephemeral=True)
                return
            except:
                pass

            # If normal stop failed, try killing the container
            try:
                subprocess.run(["docker", "kill", vps["container_id"]], check=True)
                bot.db.update_vps(token, {'status': 'stopped'})
                await ctx.send("<a:8349verify:1536290099526901850> VPS killed forcefully!", ephemeral=True)
            except subprocess.CalledProcessError as e:
                raise Exception(f"Failed to kill container: {e}")

        except Exception as e:
            await ctx.send(f"<a:8349verify:1536290099526901850> Error stopping VPS: {str(e)}", ephemeral=True)
    except Exception as e:
        logger.error(f"Error in emergency_stop: {e}")
        await ctx.send(f"<a:8349verify:1536290099526901850> Error: {str(e)}", ephemeral=True)

@bot.hybrid_command(name='emergency_remove', description='Force remove a problematic VPS (Admin only)')
@app_commands.describe(
    vps_id="ID of the VPS to remove"
)
async def emergency_remove(ctx, vps_id: str):
    """Force remove a problematic VPS (Admin only)"""
    if not has_admin_role(ctx):
        await ctx.send("<a:8349verify:1536290099526901850> You must be an admin to use this command!", ephemeral=True)
        return

    try:
        token, vps = bot.db.get_vps_by_id(vps_id)
        if not vps:
            await ctx.send("<a:8349verify:1536290099526901850> VPS not found!", ephemeral=True)
            return

        try:
            # First try to stop the container normally
            try:
                container = bot.docker_client.containers.get(vps["container_id"])
                container.stop()
            except:
                pass

            # Then try to remove it forcefully
            try:
                subprocess.run(["docker", "rm", "-f", vps["container_id"]], check=True)
            except subprocess.CalledProcessError as e:
                raise Exception(f"Failed to remove container: {e}")

            # Remove from data
            bot.db.remove_vps(token)

            await ctx.send("<a:8349verify:1536290099526901850> VPS removed forcefully!", ephemeral=True)
        except Exception as e:
            await ctx.send(f"<a:8349verify:1536290099526901850> Error removing VPS: {str(e)}", ephemeral=True)
    except Exception as e:
        logger.error(f"Error in emergency_remove: {e}")
        await ctx.send(f"<a:8349verify:1536290099526901850> Error: {str(e)}", ephemeral=True)

@bot.hybrid_command(name='suspend_vps', description='Suspend a VPS (Admin only)')
@app_commands.describe(
    vps_id="ID of the VPS to suspend"
)
async def suspend_vps(ctx, vps_id: str):
    """Suspend a VPS (Admin only)"""
    if not has_admin_role(ctx):
        await ctx.send("<a:8349verify:1536290099526901850> You must be an admin to use this command!", ephemeral=True)
        return

    try:
        token, vps = bot.db.get_vps_by_id(vps_id)
        if not vps:
            await ctx.send("<a:8349verify:1536290099526901850> VPS not found!", ephemeral=True)
            return

        if vps['status'] == 'suspended':
            await ctx.send("<a:8349verify:1536290099526901850> VPS is already suspended!", ephemeral=True)
            return

        try:
            container = bot.docker_client.containers.get(vps["container_id"])
            container.stop()
        except Exception as e:
            logger.error(f"Error stopping container for suspend: {e}")

        bot.db.update_vps(token, {'status': 'suspended'})
        await ctx.send(f"<a:8349verify:1536290099526901850> VPS {vps_id} has been suspended!")

        # Notify owner
        try:
            owner = await bot.fetch_user(int(vps['created_by']))
            await owner.send(f"[!] Your VPS {vps_id} has been suspended by an admin. Contact support for details.")
        except:
            pass

    except Exception as e:
        logger.error(f"Error in suspend_vps: {e}")
        await ctx.send(f"<a:8349verify:1536290099526901850> Error suspending VPS: {str(e)}")

@bot.hybrid_command(name='unsuspend_vps', description='Unsuspend a VPS (Admin only)')
@app_commands.describe(
    vps_id="ID of the VPS to unsuspend"
)
async def unsuspend_vps(ctx, vps_id: str):
    """Unsuspend a VPS (Admin only)"""
    if not has_admin_role(ctx):
        await ctx.send("<a:8349verify:1536290099526901850> You must be an admin to use this command!", ephemeral=True)
        return

    try:
        token, vps = bot.db.get_vps_by_id(vps_id)
        if not vps:
            await ctx.send("<a:8349verify:1536290099526901850> VPS not found!", ephemeral=True)
            return

        if vps['status'] != 'suspended':
            await ctx.send("<a:8349verify:1536290099526901850> VPS is not suspended!", ephemeral=True)
            return

        try:
            container = bot.docker_client.containers.get(vps["container_id"])
            container.start()
        except Exception as e:
            logger.error(f"Error starting container for unsuspend: {e}")
            await ctx.send(f"<a:8349verify:1536290099526901850> Error starting container: {str(e)}")
            return

        bot.db.update_vps(token, {'status': 'running'})
        await ctx.send(f"<a:8349verify:1536290099526901850> VPS {vps_id} has been unsuspended!")

        # Notify owner
        try:
            owner = await bot.fetch_user(int(vps['created_by']))
            await owner.send(f"<a:8349verify:1536290099526901850> Your VPS {vps_id} has been unsuspended by an admin.")
        except:
            pass

    except Exception as e:
        logger.error(f"Error in unsuspend_vps: {e}")
        await ctx.send(f"<a:8349verify:1536290099526901850> Error unsuspending VPS: {str(e)}")

@bot.hybrid_command(name='edit_vps', description='Edit VPS specifications (Admin only)')
@app_commands.describe(
    vps_id="ID of the VPS to edit",
    memory="New memory in GB (optional)",
    cpu="New CPU cores (optional)",
    disk="New disk space in GB (optional)"
)
async def edit_vps(ctx, vps_id: str, memory: Optional[int] = None, cpu: Optional[int] = None, disk: Optional[int] = None):
    """Edit VPS specifications (Admin only)"""
    if not has_admin_role(ctx):
        await ctx.send("<a:8349verify:1536290099526901850> You must be an admin to use this command!", ephemeral=True)
        return

    if memory is None and cpu is None and disk is None:
        await ctx.send("<a:8349verify:1536290099526901850> At least one specification to edit must be provided!", ephemeral=True)
        return

    try:
        token, vps = bot.db.get_vps_by_id(vps_id)
        if not vps:
            await ctx.send("<a:8349verify:1536290099526901850> VPS not found!", ephemeral=True)
            return

        updates = {}
        if memory is not None:
            if memory != VPS_MEMORY_GB:
                await ctx.send(
                    f"{SYMBOL['error']} The service plan is fixed at {VPS_MEMORY_GB} GB RAM.",
                    ephemeral=True
                )
                return
            updates['memory'] = memory
        if cpu is not None:
            if cpu < 1 or cpu > 32:
                await ctx.send("<a:8349verify:1536290099526901850> CPU cores must be between 1 and 32", ephemeral=True)
                return
            updates['cpu'] = cpu
        if disk is not None:
            if disk < 10 or disk > 1000:
                await ctx.send("<a:8349verify:1536290099526901850> Disk space must be between 10GB and 1000GB", ephemeral=True)
                return
            updates['disk'] = disk

        # Restart container with new limits
        try:
            container = bot.docker_client.containers.get(vps["container_id"])
            container.stop()
            container.remove()

            memory_bytes = (memory or vps['memory']) * 1024 * 1024 * 1024
            cpu_quota = int((cpu or vps['cpu']) * 100000)

            new_container = bot.docker_client.containers.run(
                vps['os_image'],
                detach=True,
                privileged=True,
                hostname=f"titancloud-{vps_id}",
                mem_limit=memory_bytes,
                cpu_period=100000,
                cpu_quota=cpu_quota,
                cap_add=["ALL"],
                command="tail -f /dev/null",
                tty=True,
                network=DOCKER_NETWORK,
                volumes={
                    f'titancloud-{vps_id}': {'bind': '/data', 'mode': 'rw'}
                },
                restart_policy={"Name": "always"}
            )

            updates['container_id'] = new_container.id
            await asyncio.sleep(5)
            setup_success, _, _ = await setup_container(
                new_container.id,
                ctx,
                memory or vps['memory'],
                vps['username'],
                vps_id=vps_id,
                use_custom_image=vps['use_custom_image']
            )
            if not setup_success:
                raise Exception("Failed to setup new container")
        except Exception as e:
            await ctx.send(f"<a:8349verify:1536290099526901850> Error updating container: {str(e)}")
            return

        bot.db.update_vps(token, updates)
        await ctx.send(f"<a:8349verify:1536290099526901850> VPS {vps_id} specifications updated successfully!")

    except Exception as e:
        logger.error(f"Error in edit_vps: {e}")
        await ctx.send(f"<a:8349verify:1536290099526901850> Error editing VPS: {str(e)}")

@bot.hybrid_command(name='ban_user', description='Ban a user from creating VPS (Admin only)')
@app_commands.describe(
    user="User to ban"
)
async def ban_user(ctx, user: discord.User):
    """Ban a user from creating VPS (Admin only)"""
    if not has_admin_role(ctx):
        await ctx.send("<a:8349verify:1536290099526901850> You must be an admin to use this command!", ephemeral=True)
        return

    bot.db.ban_user(user.id)
    await ctx.send(f"<a:8349verify:1536290099526901850> {user.mention} has been banned from creating VPS!")

@bot.hybrid_command(name='unban_user', description='Unban a user (Admin only)')
@app_commands.describe(
    user="User to unban"
)
async def unban_user(ctx, user: discord.User):
    """Unban a user (Admin only)"""
    if not has_admin_role(ctx):
        await ctx.send("<a:8349verify:1536290099526901850> You must be an admin to use this command!", ephemeral=True)
        return

    bot.db.unban_user(user.id)
    await ctx.send(f"<a:8349verify:1536290099526901850> {user.mention} has been unbanned!")

@bot.hybrid_command(name='list_banned', description='List banned users (Admin only)')
async def list_banned(ctx):
    """List banned users (Admin only)"""
    if not has_admin_role(ctx):
        await ctx.send("<a:8349verify:1536290099526901850> You must be an admin to use this command!", ephemeral=True)
        return

    banned = bot.db.get_banned_users()
    if not banned:
        await ctx.send("No banned users.", ephemeral=True)
        return

    embed = premium_embed(title="Banned Users", color=discord.Color.red())
    banned_list = []
    for user_id in banned:
        try:
            user = await bot.fetch_user(int(user_id))
            banned_list.append(f"{user.name} ({user_id})")
        except:
            banned_list.append(f"Unknown ({user_id})")
    embed.description = "\n".join(banned_list)
    await ctx.send(embed=embed, ephemeral=True)

@bot.hybrid_command(name='backup_data', description='Backup all bot data (Admin only)')
async def backup_data(ctx):
    """Backup all bot data (Admin only)"""
    if not has_admin_role(ctx):
        await ctx.send("<a:8349verify:1536290099526901850> You must be an admin to use this command!", ephemeral=True)
        return

    try:
        if bot.db.backup_data():
            await ctx.send("<a:8349verify:1536290099526901850> Data backup completed successfully!", ephemeral=True)
        else:
            await ctx.send("<a:8349verify:1536290099526901850> Failed to backup data!", ephemeral=True)
    except Exception as e:
        logger.error(f"Error in backup_data: {e}")
        await ctx.send(f"<a:8349verify:1536290099526901850> Error backing up data: {str(e)}", ephemeral=True)

@bot.hybrid_command(name='restore_data', description='Restore from backup (Admin only)')
async def restore_data(ctx):
    """Restore from backup (Admin only)"""
    if not has_admin_role(ctx):
        await ctx.send("<a:8349verify:1536290099526901850> You must be an admin to use this command!", ephemeral=True)
        return

    try:
        if bot.db.restore_data():
            await ctx.send("<a:8349verify:1536290099526901850> Data restore completed successfully!", ephemeral=True)
        else:
            await ctx.send("<a:8349verify:1536290099526901850> Failed to restore data!", ephemeral=True)
    except Exception as e:
        logger.error(f"Error in restore_data: {e}")
        await ctx.send(f"<a:8349verify:1536290099526901850> Error restoring data: {str(e)}", ephemeral=True)

class VPSManagementView(ui.View):
    def __init__(self, vps_id, container_id):
        super().__init__(timeout=300)
        self.vps_id = vps_id
        self.container_id = container_id
        self.original_message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Prevent other members from operating buttons on a user's container."""
        _, vps = bot.db.get_vps_by_id(self.vps_id)
        allowed = bool(
            vps
            and (
                vps['created_by'] == str(interaction.user.id)
                or has_admin_role(interaction)
            )
        )
        if not allowed:
            await interaction.response.send_message(
                f"{SYMBOL['error']} This control panel belongs to another user.",
                ephemeral=True
            )
        return allowed

    async def handle_missing_container(self, interaction: discord.Interaction):
        token, _ = bot.db.get_vps_by_id(self.vps_id)
        if token:
            bot.db.remove_vps(token)

        embed = premium_embed(title=f"TitanCloud VPS Management - {self.vps_id}", color=discord.Color.red())
        embed.add_field(name="Status", value="<:red_dot:1536292733528838195> Container Not Found", inline=True)
        embed.add_field(name="Note", value="This VPS instance is no longer available. Please create a new one.", inline=False)

        for item in self.children:
            item.disabled = True

        await interaction.message.edit(embed=embed, view=self)
        if interaction.response.is_done():
            await interaction.followup.send(f"{SYMBOL['error']} This VPS is no longer available.", ephemeral=True)
        else:
            await interaction.response.send_message(f"{SYMBOL['error']} This VPS is no longer available.", ephemeral=True)

    @discord.ui.button(label="START", emoji=EMOJI_START, style=discord.ButtonStyle.green)
    async def start_vps(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)

            try:
                container = await docker_call(bot.docker_client.containers.get, self.container_id)
            except docker.errors.NotFound:
                await self.handle_missing_container(interaction)
                return

            token, vps = bot.db.get_vps_by_id(self.vps_id)
            if vps['status'] == 'suspended':
                await interaction.followup.send("<a:8349verify:1536290099526901850> This VPS is suspended. Contact admin to unsuspend.", ephemeral=True)
                return

            if container.status == "running":
                await interaction.followup.send("VPS is already running!", ephemeral=True)
                return

            await docker_call(container.start)
            await asyncio.sleep(5)

            if token:
                bot.db.update_vps(token, {'status': 'running'})

            embed = premium_embed(title=f"TitanCloud VPS Management - {self.vps_id}", color=discord.Color.green())
            embed.add_field(name="Status", value="<a:green_loading:1536292254195650601> Running", inline=True)

            if vps:
                embed.add_field(name="Memory", value=f"{vps['memory']}GB", inline=True)
                embed.add_field(name="CPU", value=f"{vps['cpu']} cores", inline=True)
                embed.add_field(name="Disk", value=f"{vps['disk']}GB", inline=True)
                embed.add_field(name="Username", value=vps['username'], inline=True)
                embed.add_field(name="Created", value=vps['created_at'], inline=True)

            await interaction.message.edit(embed=embed)
            await interaction.followup.send("<a:8349verify:1536290099526901850> TitanCloud VPS started successfully!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"<a:8349verify:1536290099526901850> Error starting VPS: {str(e)}", ephemeral=True)

    @discord.ui.button(label="STOP", emoji=EMOJI_STOP, style=discord.ButtonStyle.red)
    async def stop_vps(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)

            try:
                container = await docker_call(bot.docker_client.containers.get, self.container_id)
            except docker.errors.NotFound:
                await self.handle_missing_container(interaction)
                return

            if container.status != "running":
                await interaction.followup.send("VPS is already stopped!", ephemeral=True)
                return

            await docker_call(container.stop)

            token, vps = bot.db.get_vps_by_id(self.vps_id)
            if token:
                bot.db.update_vps(token, {'status': 'stopped'})

            embed = premium_embed(title=f"TitanCloud VPS Management - {self.vps_id}", color=discord.Color.orange())
            embed.add_field(name="Status", value="<:red_dot:1536292733528838195> Stopped", inline=True)

            if vps:
                embed.add_field(name="Memory", value=f"{vps['memory']}GB", inline=True)
                embed.add_field(name="CPU", value=f"{vps['cpu']} cores", inline=True)
                embed.add_field(name="Disk", value=f"{vps['disk']}GB", inline=True)
                embed.add_field(name="Username", value=vps['username'], inline=True)
                embed.add_field(name="Created", value=vps['created_at'], inline=True)

            await interaction.message.edit(embed=embed)
            await interaction.followup.send("<a:8349verify:1536290099526901850> TitanCloud VPS stopped successfully!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"<a:8349verify:1536290099526901850> Error stopping VPS: {str(e)}", ephemeral=True)

    @discord.ui.button(label="RESTART", emoji=EMOJI_RESTART, style=discord.ButtonStyle.blurple)
    async def restart_vps(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)

            try:
                container = await docker_call(bot.docker_client.containers.get, self.container_id)
            except docker.errors.NotFound:
                await self.handle_missing_container(interaction)
                return

            token, vps = bot.db.get_vps_by_id(self.vps_id)
            if vps['status'] == 'suspended':
                await interaction.followup.send("<a:8349verify:1536290099526901850> This VPS is suspended. Contact admin to unsuspend.", ephemeral=True)
                return

            await docker_call(container.restart)
            await asyncio.sleep(5)

            # Update restart count in VPS data
            if token:
                updates = {
                    'restart_count': vps.get('restart_count', 0) + 1,
                    'last_restart': str(datetime.datetime.now()),
                    'status': 'running'
                }
                bot.db.update_vps(token, updates)

                bot.db.increment_stat('total_restarts')

                # Get new SSH session
                try:
                    ssh_session_line = await generate_fresh_ssh_session(self.container_id)
                    if ssh_session_line:
                        bot.db.update_vps(token, {'tmate_session': ssh_session_line})

                        # Send new SSH details to owner
                        try:
                            owner = await bot.fetch_user(int(vps["created_by"]))
                            embed = premium_embed(title=f"TitanCloud VPS Restarted - {self.vps_id}", color=discord.Color.blue())
                            embed.add_field(name="New SSH Session", value=f"```{ssh_session_line}```", inline=False)
                            await owner.send(embed=embed)
                        except:
                            pass
                except:
                    pass

            embed = premium_embed(title=f"TitanCloud VPS Management - {self.vps_id}", color=discord.Color.green())
            embed.add_field(name="Status", value="<a:green_loading:1536292254195650601> Running", inline=True)

            if vps:
                embed.add_field(name="Memory", value=f"{vps['memory']}GB", inline=True)
                embed.add_field(name="CPU", value=f"{vps['cpu']} cores", inline=True)
                embed.add_field(name="Disk", value=f"{vps['disk']}GB", inline=True)
                embed.add_field(name="Username", value=vps['username'], inline=True)
                embed.add_field(name="Created", value=vps['created_at'], inline=True)
                embed.add_field(name="Restart Count", value=vps.get('restart_count', 0) + 1, inline=True)

            await interaction.message.edit(embed=embed, view=VPSManagementView(self.vps_id, container.id))
            await interaction.followup.send("<a:8349verify:1536290099526901850> TitanCloud VPS restarted successfully! New SSH details sent to owner.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"<a:8349verify:1536290099526901850> Error restarting VPS: {str(e)}", ephemeral=True)

    @discord.ui.button(label="REINSTALL CURRENT OS", emoji=EMOJI_STOP, style=discord.ButtonStyle.grey)
    async def reinstall_os(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            token, vps = bot.db.get_vps_by_id(self.vps_id)
            if not vps:
                await self.handle_missing_container(interaction)
                return

            worker = OSSelectionView(self.vps_id, self.container_id, interaction.message)
            current_image = vps.get('os_image') or DEFAULT_OS_IMAGE
            await worker.reinstall_os(interaction, current_image)
        except Exception as e:
            if interaction.response.is_done():
                await interaction.followup.send(f"{SYMBOL['error']} Reinstall failed: {str(e)}", ephemeral=True)
            else:
                await interaction.response.send_message(f"{SYMBOL['error']} Reinstall failed: {str(e)}", ephemeral=True)

    @discord.ui.button(label="FRESH SSH", emoji=EMOJI_MANAGE, style=discord.ButtonStyle.blurple)
    async def ssh_access(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Generate and DM a brand-new tmate SSH session on every click."""
        await interaction.response.defer(ephemeral=True)
        token, vps = bot.db.get_vps_by_id(self.vps_id)
        if not vps:
            await interaction.followup.send(f"{SYMBOL['error']} VPS not found.", ephemeral=True)
            return

        try:
            container = await docker_call(bot.docker_client.containers.get, vps['container_id'])
            await docker_call(container.reload)
            if container.status != 'running':
                await docker_call(container.start)
                await asyncio.sleep(3)
                bot.db.update_vps(token, {'status': 'running'})

            await interaction.followup.send(
                f"{EMOJI_LOADING} Generating a fresh one-time SSH session...",
                ephemeral=True
            )
            ssh_session = await generate_fresh_ssh_session(container.id)
            bot.db.update_vps(token, {'tmate_session': ssh_session})
        except Exception as exc:
            logger.error(f"Fresh SSH generation failed for {self.vps_id}: {exc}")
            await interaction.followup.send(
                f"{SYMBOL['error']} Could not generate a new SSH session. Try again shortly.",
                ephemeral=True
            )
            return

        embed = premium_embed(
            f"SSH ACCESS  /  {self.vps_id}",
            "A new session was generated for this request. Previous tmate sessions are closed.",
            BRAND_COLOR
        )
        embed.add_field(name="USERNAME", value=f"`{vps['username']}`", inline=True)
        embed.add_field(name="USER PASSWORD", value=f"||{vps.get('password', 'Not set')}||", inline=False)
        if vps.get('root_password'):
            embed.add_field(name="ROOT PASSWORD", value=f"||{vps['root_password']}||", inline=False)
        embed.add_field(
            name="TMATE SSH SESSION",
            value=f"```{ssh_session}```",
            inline=False
        )
        embed.add_field(name="ACCESS TOKEN", value=f"||{token}||", inline=False)

        try:
            await interaction.user.send(embed=embed)
            await interaction.followup.send(
                f"{SYMBOL['success']} SSH access details were sent to your DMs.",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"{SYMBOL['error']} I could not DM you. Enable DMs from server members and try again.",
                ephemeral=True
            )

    @discord.ui.button(label="TRANSFER", emoji=EMOJI_MANAGE, style=discord.ButtonStyle.grey)
    async def transfer_vps(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = TransferVPSModal(self.vps_id)
        await interaction.response.send_modal(modal)

class OSSelectionView(ui.View):
    def __init__(self, vps_id, container_id, original_message):
        super().__init__(timeout=300)
        self.vps_id = vps_id
        self.container_id = container_id
        self.original_message = original_message

    async def reinstall_os(self, interaction: discord.Interaction, image: str):
        token, vps = bot.db.get_vps_by_id(self.vps_id)
        if not vps:
            await interaction.response.send_message(f"{SYMBOL['error']} VPS not found.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        status_msg = await interaction.followup.send(
            f"{EMOJI_STOP} Reinstalling `{image}` on the current VPS...",
            ephemeral=True
        )
        old_container = None
        new_container = None
        committed = False

        try:
            old_container = await docker_call(bot.docker_client.containers.get, self.container_id)
            memory_bytes = vps['memory'] * 1024 * 1024 * 1024
            data_volume = next(
                (
                    mount.get('Name')
                    for mount in old_container.attrs.get('Mounts', [])
                    if mount.get('Destination') == '/data' and mount.get('Name')
                ),
                f'titancloud-{self.vps_id}'
            )
            run_options = {
                'detach': True,
                'privileged': True,
                'hostname': f"titancloud-{self.vps_id}",
                'mem_limit': memory_bytes,
                'cpu_period': 100000,
                'cpu_quota': int(vps['cpu'] * 100000),
                'cap_add': ["ALL"],
                'command': "tail -f /dev/null",
                'tty': True,
                'network': DOCKER_NETWORK,
                'volumes': {
                    data_volume: {'bind': '/data', 'mode': 'rw'}
                },
                'restart_policy': {"Name": "always"}
            }

            try:
                new_container = await docker_call(
                    bot.docker_client.containers.run,
                    image,
                    **run_options
                )
            except docker.errors.ImageNotFound:
                await status_msg.edit(content=f"{EMOJI_LOADING} Pulling the current OS image `{image}`...")
                await docker_call(bot.docker_client.images.pull, image)
                new_container = await docker_call(
                    bot.docker_client.containers.run,
                    image,
                    **run_options
                )

            setup_success, ssh_password, _ = await setup_container(
                new_container.id,
                status_msg,
                vps['memory'],
                vps['username'],
                vps_id=self.vps_id,
                use_custom_image=False
            )
            if not setup_success:
                raise RuntimeError("The replacement container could not be configured")

            ssh_session = await generate_fresh_ssh_session(new_container.id)

            # Commit the replacement only after it is fully working. The old
            # container remains available if any earlier step fails.
            try:
                await docker_call(old_container.stop)
                await docker_call(old_container.remove)
            except Exception as exc:
                logger.warning(f"Replacement succeeded but old container cleanup failed: {exc}")

            bot.db.update_vps(token, {
                'container_id': new_container.id,
                'password': ssh_password,
                'tmate_session': ssh_session,
                'status': 'running'
            })
            committed = True

            embed = premium_embed(
                f"REINSTALL COMPLETE  /  {self.vps_id}",
                "The same operating system was installed successfully.",
                SUCCESS_COLOR
            )
            embed.add_field(name="OPERATING SYSTEM", value=f"`{image}`", inline=True)
            embed.add_field(name="MEMORY", value=f"`{vps['memory']} GB`", inline=True)
            embed.add_field(name="NEW SSH SESSION", value=f"```{ssh_session}```", inline=False)
            embed.add_field(name="NEW PASSWORD", value=f"||{ssh_password}||", inline=False)

            try:
                owner = await bot.fetch_user(int(vps['created_by']))
                await owner.send(embed=embed)
            except discord.HTTPException as exc:
                logger.warning(f"Could not DM reinstall details: {exc}")

            await status_msg.edit(content=f"{EMOJI_VERIFY} Current OS reinstalled successfully. Details sent by DM.")
            await self.original_message.edit(
                embed=embed,
                view=VPSManagementView(self.vps_id, new_container.id)
            )
        except Exception as exc:
            logger.error(f"Reinstall failed for {self.vps_id}: {exc}")
            if committed:
                try:
                    await interaction.followup.send(
                        f"{EMOJI_VERIFY} Reinstall completed, but a Discord status update failed.",
                        ephemeral=True
                    )
                except discord.HTTPException:
                    pass
                return
            if new_container is not None and not committed:
                try:
                    await docker_call(new_container.stop)
                    await docker_call(new_container.remove)
                except Exception:
                    pass
            await status_msg.edit(
                content=f"{SYMBOL['error']} Reinstall failed. Your previous VPS was kept online."
            )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.original_message.edit(view=self)
        except:
            pass

class TransferVPSModal(ui.Modal, title='Transfer VPS'):
    def __init__(self, vps_id: str):
        super().__init__()
        self.vps_id = vps_id
        self.new_owner = ui.TextInput(
            label='New Owner',
            placeholder='Enter user ID or @mention',
            required=True
        )
        self.add_item(self.new_owner)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            new_owner_input = self.new_owner.value.strip()

            # Extract user ID from mention if provided
            if new_owner_input.startswith('<@') and new_owner_input.endswith('>'):
                new_owner_id = new_owner_input[2:-1]
                if new_owner_id.startswith('!'):  # Handle nickname mentions
                    new_owner_id = new_owner_id[1:]
            else:
                # Validate it's a numeric ID
                if not new_owner_input.isdigit():
                    await interaction.response.send_message("<a:8349verify:1536290099526901850> Please provide a valid user ID or @mention", ephemeral=True)
                    return
                new_owner_id = new_owner_input

            token, vps = bot.db.get_vps_by_id(self.vps_id)
            if not vps or vps["created_by"] != str(interaction.user.id):
                await interaction.response.send_message("<a:8349verify:1536290099526901850> VPS not found or you don't have permission to transfer it!", ephemeral=True)
                return

            try:
                old_owner = await bot.fetch_user(int(vps["created_by"]))
                old_owner_name = old_owner.name
            except:
                old_owner_name = "Unknown User"

            try:
                new_owner = await bot.fetch_user(int(new_owner_id))
                new_owner_name = new_owner.name

                member = interaction.guild.get_member(new_owner.id) if interaction.guild else None
                if not member or not is_service_user(member):
                    await interaction.response.send_message(
                        f"{SYMBOL['error']} The new owner needs the <@&{USER_ROLE_ID}> role.",
                        ephemeral=True
                    )
                    return

                # Check if new owner is banned
                if bot.db.is_user_banned(new_owner.id):
                    await interaction.response.send_message(f"<a:8349verify:1536290099526901850> {new_owner.mention} is banned!", ephemeral=True)
                    return

                # Check if new owner already has max VPS
                if bot.db.get_user_vps_count(new_owner.id) >= bot.db.get_setting('max_vps_per_user'):
                    await interaction.response.send_message(f"<a:8349verify:1536290099526901850> {new_owner.mention} already has the maximum number of VPS instances ({bot.db.get_setting('max_vps_per_user')})", ephemeral=True)
                    return
            except:
                await interaction.response.send_message("<a:8349verify:1536290099526901850> Invalid user ID or mention!", ephemeral=True)
                return

            bot.db.update_vps(token, {"created_by": str(new_owner.id)})

            await interaction.response.send_message(f"<a:8349verify:1536290099526901850> TitanCloud VPS {self.vps_id} has been transferred from {old_owner_name} to {new_owner_name}!", ephemeral=True)

            try:
                embed = premium_embed(title="TitanCloud VPS Transferred to You", color=discord.Color.green())
                embed.add_field(name="VPS ID", value=self.vps_id, inline=True)
                embed.add_field(name="Previous Owner", value=old_owner_name, inline=True)
                embed.add_field(name="Memory", value=f"{vps['memory']}GB", inline=True)
                embed.add_field(name="CPU", value=f"{vps['cpu']} cores", inline=True)
                embed.add_field(name="Disk", value=f"{vps['disk']}GB", inline=True)
                embed.add_field(name="Username", value=vps['username'], inline=True)
                embed.add_field(name="Access Token", value=token, inline=False)
                embed.add_field(name="SSH Password", value=f"||{vps.get('password', 'Not set')}||", inline=False)
                await new_owner.send(embed=embed)
            except:
                await interaction.followup.send("Note: Could not send DM to the new owner.", ephemeral=True)

        except Exception as e:
            logger.error(f"Error in TransferVPSModal: {e}")
            await interaction.response.send_message(f"<a:8349verify:1536290099526901850> Error transferring VPS: {str(e)}", ephemeral=True)

@bot.hybrid_command(name='manage', description='Open the premium VPS control panel')
@app_commands.describe(
    vps_id="VPS ID; optional when you own exactly one VPS"
)
async def manage(ctx, vps_id: Optional[str] = None):
    """Manage a VPS instance"""
    await defer_context(ctx)
    try:
        if not vps_id:
            owned_vps = bot.db.get_user_vps(ctx.author.id)
            if not owned_vps:
                await ctx.send(f"{SYMBOL['info']} You do not have a VPS to manage.", ephemeral=True)
                return
            if len(owned_vps) > 1:
                ids = "\n".join(f"`{item['vps_id']}`" for item in owned_vps[:20])
                embed = premium_embed(
                    "SELECT A VPS",
                    "You own multiple containers. Run `/manage <vps_id>` with one of these IDs."
                )
                embed.add_field(name="AVAILABLE VPS IDS", value=ids, inline=False)
                await ctx.send(embed=embed, ephemeral=True)
                return
            vps_id = owned_vps[0]['vps_id']

        token, vps = bot.db.get_vps_by_id(vps_id)
        if not vps or (vps["created_by"] != str(ctx.author.id) and not has_admin_role(ctx)):
            await ctx.send("<a:8349verify:1536290099526901850> VPS not found or you don't have access to it!", ephemeral=True)
            return

        try:
            container = await docker_call(bot.docker_client.containers.get, vps["container_id"])
            container_status = container.status.capitalize()
        except:
            container_status = "Not Found"

        status = vps['status'].capitalize()

        embed = premium_embed(
            title=f"CONTROL PANEL  /  {vps_id}",
            description="Use the controls below to operate your private container.",
            color=BRAND_COLOR
        )
        embed.add_field(name="STATUS", value=f"`{status}`  /  Docker `{container_status}`", inline=False)
        embed.add_field(name="MEMORY", value=f"`{vps['memory']} GB`", inline=True)
        embed.add_field(name="COMPUTE", value=f"`{vps['cpu']} vCPU`", inline=True)
        embed.add_field(name="STORAGE", value=f"`{vps['disk']} GB`", inline=True)
        embed.add_field(name="USERNAME", value=f"`{vps['username']}`", inline=True)
        embed.add_field(name="SYSTEM", value=f"`{vps.get('os_image', DEFAULT_OS_IMAGE)}`", inline=True)
        embed.add_field(name="RESTARTS", value=f"`{vps.get('restart_count', 0)}`", inline=True)
        embed.add_field(name="CREATED", value=vps['created_at'], inline=False)

        view = VPSManagementView(vps_id, vps["container_id"])

        message = await ctx.send(embed=embed, view=view)
        view.original_message = message

        try:
            dm_view = VPSManagementView(vps_id, vps["container_id"])
            dm_message = await ctx.author.send(embed=embed, view=dm_view)
            dm_view.original_message = dm_message
        except discord.Forbidden:
            await ctx.send(
                f"{SYMBOL['info']} I could not DM the control panel. Enable server DMs to receive it privately.",
                ephemeral=True
            )
    except Exception as e:
        logger.error(f"Error in manage: {e}")
        await ctx.send(f"<a:8349verify:1536290099526901850> Error managing VPS: {str(e)}", ephemeral=True)

@bot.hybrid_command(name='transfer_vps', description='Transfer a VPS to another user')
@app_commands.describe(
    vps_id="ID of the VPS to transfer",
    new_owner="User to transfer the VPS to"
)
async def transfer_vps_command(ctx, vps_id: str, new_owner: discord.Member):
    """Transfer a VPS to another user"""
    try:
        if not is_service_user(new_owner):
            await ctx.send(
                f"{SYMBOL['error']} {new_owner.mention} needs the <@&{USER_ROLE_ID}> role.",
                ephemeral=True
            )
            return

        token, vps = bot.db.get_vps_by_id(vps_id)
        if not vps or vps["created_by"] != str(ctx.author.id):
            await ctx.send("<a:8349verify:1536290099526901850> VPS not found or you don't have permission to transfer it!", ephemeral=True)
            return

        if bot.db.is_user_banned(new_owner.id):
            await ctx.send("<a:8349verify:1536290099526901850> This user is banned!", ephemeral=True)
            return

        # Check if new owner already has max VPS
        if bot.db.get_user_vps_count(new_owner.id) >= bot.db.get_setting('max_vps_per_user'):
            await ctx.send(f"<a:8349verify:1536290099526901850> {new_owner.mention} already has the maximum number of VPS instances ({bot.db.get_setting('max_vps_per_user')})", ephemeral=True)
            return

        bot.db.update_vps(token, {"created_by": str(new_owner.id)})

        await ctx.send(f"<a:8349verify:1536290099526901850> TitanCloud VPS {vps_id} has been transferred from {ctx.author.name} to {new_owner.name}!")

        try:
            embed = premium_embed(title="TitanCloud VPS Transferred to You", color=discord.Color.green())
            embed.add_field(name="VPS ID", value=vps_id, inline=True)
            embed.add_field(name="Previous Owner", value=ctx.author.name, inline=True)
            embed.add_field(name="Memory", value=f"{vps['memory']}GB", inline=True)
            embed.add_field(name="CPU", value=f"{vps['cpu']} cores", inline=True)
            embed.add_field(name="Disk", value=f"{vps['disk']}GB", inline=True)
            embed.add_field(name="Username", value=vps['username'], inline=True)
            embed.add_field(name="Access Token", value=token, inline=False)
            embed.add_field(name="SSH Password", value=f"||{vps.get('password', 'Not set')}||", inline=False)
            await new_owner.send(embed=embed)
        except:
            await ctx.send("Note: Could not send DM to the new owner.", ephemeral=True)

    except Exception as e:
        logger.error(f"Error in transfer_vps_command: {e}")
        await ctx.send(f"<a:8349verify:1536290099526901850> Error transferring VPS: {str(e)}", ephemeral=True)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await safe_ctx_send(ctx, f"{SYMBOL['error']} You don't have permission to use this command!", ephemeral=True)
    elif isinstance(error, commands.CommandNotFound):
        await safe_ctx_send(ctx, f"{SYMBOL['error']} Command not found. Use `/help`.", ephemeral=True)
    elif isinstance(error, commands.MissingRequiredArgument):
        await safe_ctx_send(ctx, f"{SYMBOL['error']} Missing argument: {error.param.name}", ephemeral=True)
    else:
        logger.error(f"Command error: {error}")
        await safe_ctx_send(ctx, f"{SYMBOL['error']} Request failed. Please try again.", ephemeral=True)

# Run the bot
if __name__ == "__main__":
    try:
        if not TOKEN or TOKEN == 'replace_with_your_bot_token':
            raise RuntimeError(
                "DISCORD_TOKEN is missing or still uses the placeholder value. "
                "Reset the bot token in the Discord Developer Portal and update .env."
            )

        # Create directories if they don't exist
        os.makedirs("temp_dockerfiles", exist_ok=True)
        os.makedirs("migrations", exist_ok=True)

        bot.run(TOKEN)
    except discord.LoginFailure:
        logger.error(
            "Discord rejected DISCORD_TOKEN (401 Unauthorized). Reset the token in "
            "Discord Developer Portal > Applications > Bot, update .env, and restart."
        )
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
        traceback.print_exc()
