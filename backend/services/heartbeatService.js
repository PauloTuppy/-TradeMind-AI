class HeartbeatService {
  constructor(io) {
    this.io = io;
    this.clients = new Map();
    this.heartbeatInterval = 30000; // 30 seconds
    
    // Start heartbeat monitoring
    setInterval(() => this.checkHeartbeats(), this.heartbeatInterval);
  }
  
  registerClient(socketId) {
    this.clients.set(socketId, {
      lastHeartbeat: Date.now(),
      missedHeartbeats: 0
    });
  }
  
  heartbeat(socketId) {
    if (this.clients.has(socketId)) {
      const client = this.clients.get(socketId);
      client.lastHeartbeat = Date.now();
      client.missedHeartbeats = 0;
      this.clients.set(socketId, client);
    }
  }
  
  removeClient(socketId) {
    this.clients.delete(socketId);
  }
  
  checkHeartbeats() {
    const now = Date.now();
    const maxMissedHeartbeats = 3;
    
    this.clients.forEach((client, socketId) => {
      // If last heartbeat was more than interval ago
      if (now - client.lastHeartbeat > this.heartbeatInterval) {
        client.missedHeartbeats++;
        
        // If client missed too many heartbeats
        if (client.missedHeartbeats >= maxMissedHeartbeats) {
          // Disconnect the socket
          const socket = this.io.sockets.sockets.get(socketId);
          if (socket) {
            console.log(`Disconnecting inactive client: ${socketId}`);
            socket.disconnect(true);
          }
          this.clients.delete(socketId);
        } else {
          this.clients.set(socketId, client);
        }
      }
    });
  }
}

module.exports = HeartbeatService;