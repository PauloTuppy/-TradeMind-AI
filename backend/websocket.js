const WebSocket = require('ws');
const redis = require('redis');
const { authenticateWS } = require('./middleware/auth');

const createWebSocketServer = (server) => {
  const wss = new WebSocket.Server({ server });
  const redisClient = redis.createClient(process.env.REDIS_URL);

  // Subscribe to market data channels
  redisClient.subscribe('crypto-prices', 'order-book-updates');

  wss.on('connection', (ws, req) => {
    // Authentication for WebSocket
    authenticateWS(req, (err, user) => {
      if (err) return ws.close(1008, err.message);
      
      // Client-specific channels
      redisClient.subscribe(`user-${user.id}-alerts`);
      
      ws.on('message', (message) => {
        // Handle client subscriptions
        handleSubscription(message, user);
      });

      // Broadcast Redis messages to WebSocket clients
      redisClient.on('message', (channel, message) => {
        if (shouldSendToClient(channel, user)) {
          ws.send(JSON.stringify({ channel, data: JSON.parse(message) }));
        }
      });
    });
  });

  return wss;
};

// Helper functions
function handleSubscription(message, user) {
  try {
    const data = JSON.parse(message);
    // Process subscription requests
    // Example: subscribe to specific trading pairs
  } catch (error) {
    console.error('Invalid message format:', error);
  }
}

function shouldSendToClient(channel, user) {
  // Logic to determine if this channel's messages should be sent to this user
  return channel === 'crypto-prices' || 
         channel === 'order-book-updates' || 
         channel === `user-${user.id}-alerts`;
}

module.exports = createWebSocketServer;