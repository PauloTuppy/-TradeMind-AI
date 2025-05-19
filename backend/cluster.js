const cluster = require('cluster');
const http = require('http');
const numCPUs = require('os').cpus().length;
const { setupMaster, setupWorker } = require('@socket.io/sticky');
const { createAdapter, setupPrimary } = require('@socket.io/cluster-adapter');
const express = require('express');
const redis = require('redis');
const { Server } = require('socket.io');

if (cluster.isMaster) {
  console.log(`Master ${process.pid} is running`);

  // Create an HTTP server
  const httpServer = http.createServer();
  
  // Set up sticky sessions
  setupMaster(httpServer, {
    loadBalancingMethod: "least-connection"
  });
  
  // Set up the cluster adapter
  setupPrimary();
  
  // Fork workers
  for (let i = 0; i < numCPUs; i++) {
    cluster.fork();
  }
  
  // Handle worker events
  cluster.on('exit', (worker, code, signal) => {
    console.log(`Worker ${worker.process.pid} died`);
    // Replace the dead worker
    cluster.fork();
  });
  
  // Start listening
  httpServer.listen(3001);
} else {
  console.log(`Worker ${process.pid} started`);
  
  // Create Express app
  const app = express();
  const server = http.createServer(app);
  
  // Set up Socket.IO with the cluster adapter
  const io = new Server(server, {
    cors: {
      origin: process.env.FRONTEND_URL || "http://localhost:3000",
      methods: ["GET", "POST"],
      credentials: true
    },
    adapter: createAdapter()
  });
  
  // Set up worker connection
  setupWorker(io);
  
  // Set up Redis client for pub/sub
  const redisClient = redis.createClient(process.env.REDIS_URL);
  
  // Subscribe to channels
  redisClient.subscribe('crypto-prices', 'order-book-updates');
  
  // Handle Redis messages
  redisClient.on('message', (channel, message) => {
    // Broadcast to all connected clients in the appropriate room
    io.to(channel).emit('data', JSON.parse(message));
  });
  
  // Handle socket connections
  io.on('connection', (socket) => {
    console.log(`Client connected: ${socket.id}`);
    
    // Handle client subscriptions
    socket.on('subscribe', (channels) => {
      if (Array.isArray(channels)) {
        channels.forEach(channel => {
          socket.join(channel);
        });
      }
    });
    
    // Handle client unsubscriptions
    socket.on('unsubscribe', (channels) => {
      if (Array.isArray(channels)) {
        channels.forEach(channel => {
          socket.leave(channel);
        });
      }
    });
    
    // Handle authentication
    socket.on('authenticate', (token) => {
      // Verify JWT token
      // If valid, subscribe to user-specific channels
    });
    
    // Handle disconnection
    socket.on('disconnect', () => {
      console.log(`Client disconnected: ${socket.id}`);
    });
  });
  
  // Set up API routes
  app.use(express.json());
  
  // Health check endpoint
  app.get('/health', (req, res) => {
    res.json({ status: 'healthy', workerId: process.pid });
  });
  
  // Include other routes
  app.use('/api', require('./routes'));
}