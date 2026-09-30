/** Mounts every route group under /api and exposes a health check. */
import { Router } from "express";

import { env } from "../config/env.js";
import { UNIVERSITIES } from "../data/universities.js";
import authRoutes from "./auth.routes.js";
import organisationRoutes from "./organisations.routes.js";
import scanRoutes from "./scans.routes.js";
import subscriptionRoutes from "./subscription.routes.js";
import userRoutes from "./users.routes.js";

const router = Router();

/**
 * GET /api/health
 *
 * Reports whether the optional AI layer is configured, which is the question that comes
 * up most when explanations stop appearing in the desktop app.
 */
router.get("/health", (req, res) => {
  res.json({
    status: "ok",
    environment: env.nodeEnv,
    aiExplanations: env.groq.enabled ? "enabled" : "disabled",
    // Lets a test suite refuse to run against a live project, where it would leave
    // real accounts behind.
    database: env.usingEmulators ? "emulator" : "live",
    time: new Date().toISOString(),
  });
});

/**
 * GET /api/universities
 *
 * The list students pick from when registering. Public, since it is needed before there
 * is an account, and contains nothing but names and email domains.
 */
router.get("/universities", (req, res) => {
  res.set("Cache-Control", "public, max-age=3600");
  res.json({ universities: UNIVERSITIES });
});

router.use("/auth", authRoutes);
router.use("/users", userRoutes);
router.use("/scans", scanRoutes);
router.use("/organisations", organisationRoutes);
router.use("/subscription", subscriptionRoutes);

export default router;
