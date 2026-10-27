/**
 * An error that carries an HTTP status, so route code can throw a meaningful failure
 * and let the central error handler turn it into a response.
 */
export class ApiError extends Error {
  constructor(status, message, details = undefined, code = undefined) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.details = details;
    // A stable machine-readable reason, for the few refusals a client must act on
    // rather than just display (such as an organisation whose subscription ended).
    this.code = code;
  }

  static unavailable(message) {
    return new ApiError(503, message);
  }

  static badRequest(message, details) {
    return new ApiError(400, message, details);
  }

  static unauthorized(message = "Authentication required.") {
    return new ApiError(401, message);
  }

  static forbidden(message = "You do not have permission to do that.") {
    return new ApiError(403, message);
  }

  static notFound(message = "Not found.") {
    return new ApiError(404, message);
  }

  static conflict(message) {
    return new ApiError(409, message);
  }

  static tooManyRequests(message = "Too many requests. Try again shortly.") {
    return new ApiError(429, message);
  }

  static internal(message = "Something went wrong on our side.") {
    return new ApiError(500, message);
  }
}
