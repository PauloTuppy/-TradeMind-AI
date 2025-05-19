const redis = require('redis');
const { RateLimiterRedis } = require('rate-limiter-flexible');

// Create Redis client
const redisClient = redis.createClient(process.env.REDIS_URL);

// Setup rate limiter options
const rateLimiterOptions = {
  storeClient: redisClient,
  keyPrefix: 'middleware',
  points: 10, // Number of points
  duration: 1, // Per second
};

const rateLimiterMiddleware = new RateLimiterRedis(rateLimiterOptions);

// Express middleware
const rateLimiter = (req, res, next) => {
  // Use IP as key for rate limiting
  rateLimiterMiddleware.consume(req.ip)
    .then(() => {
      next();
    })
    .catch(() => {
      res.status(429).json({error: 'Too Many Requests'});
    });
};

// WebSocket rate limiter
const wsRateLimiter = async (socket, next) => {
  try {
    await rateLimiterMiddleware.consume(socket.handshake.address);
    next();
  } catch {
    next(new Error('Too many requests'));
  }
};

module.exports = { rateLimiter, wsRateLimiter };