// Custom hook for managing simulation WebSocket connection

import { useState, useEffect, useCallback, useRef } from "react";
import { io, Socket } from "socket.io-client";
import {
  SimulationState,
  GridScenarioConfig,
  SimulationAgentsConfig,
} from "../types/simulation";

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL ?? "http://localhost:8000";

export type BackendStatus = "unknown" | "waking" | "online" | "offline";

/** Stable per-tab session ID (survives re-renders, gone when tab closes). */
const getSessionId = (): string => {
  const key = "wsis-session-id";
  let id = sessionStorage.getItem(key);
  if (!id) {
    id = crypto.randomUUID();
    sessionStorage.setItem(key, id);
  }
  return id;
};

const SESSION_ID = getSessionId();

/** Headers included in every REST request. */
const SESSION_HEADERS = {
  "Content-Type": "application/json",
  "X-Session-ID": SESSION_ID,
};

export const useSimulation = () => {
  const [state, setState] = useState<SimulationState | null>(null);
  const [connected, setConnected] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [isLoaded, setIsLoaded] = useState(false);
  const [backendStatus, setBackendStatus] = useState<BackendStatus>("unknown");
  const socketRef = useRef<Socket | null>(null);

  useEffect(() => {
    // Connect to WebSocket with session ID so the server routes events correctly
    const socket = io(BACKEND_URL, {
      transports: ["websocket", "polling"],
      auth: { sessionId: SESSION_ID },
    });

    socketRef.current = socket;

    socket.on("connect", () => {
      console.log("WebSocket connected");
      setConnected(true);
    });

    socket.on("disconnect", () => {
      console.log("WebSocket disconnected");
      setConnected(false);
    });

    socket.on("simulation_state", (data: SimulationState) => {
      setState(data);
      if (data.status) {
        setIsRunning(data.status.running);
        setIsPaused(data.status.paused);
      }
    });

    socket.on("simulation_complete", (data) => {
      console.log("Simulation complete:", data);
      setIsRunning(false);
    });

    socket.on("simulation_paused", () => {
      setIsPaused(true);
    });

    socket.on("simulation_resumed", () => {
      setIsPaused(false);
    });

    socket.on("simulation_stopped", () => {
      setIsRunning(false);
      setIsPaused(false);
    });

    socket.on("simulation_reset", () => {
      // Step 0 state is broadcast by the backend before this event,
      // so keep isLoaded=true so the user can press Start again.
      setIsRunning(false);
      setIsPaused(false);
      setIsLoaded(true);
    });

    return () => {
      socket.disconnect();
    };
  }, []);

  // Initial health check on mount
  useEffect(() => {
    fetch(`${BACKEND_URL}/api/health`)
      .then((r) =>
        r.ok ? setBackendStatus("online") : setBackendStatus("offline"),
      )
      .catch(() => setBackendStatus("offline"));
  }, []);

  /**
   * Poll /api/health until the backend responds (Render cold start ~30 s).
   * Retries every 3 s for up to 60 s total.
   */
  const wakeBackend = useCallback(async () => {
    setBackendStatus("waking");
    const MAX_ATTEMPTS = 20; // 20 × 3 s = 60 s
    const INTERVAL_MS = 3000;

    for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
      try {
        const res = await fetch(`${BACKEND_URL}/api/health`);
        if (res.ok) {
          setBackendStatus("online");
          // socket.io auto-reconnects; nudge it if it's still disconnected
          socketRef.current?.connect();
          return;
        }
      } catch {
        // backend still sleeping — swallow and retry
      }
      await new Promise<void>((resolve) => setTimeout(resolve, INTERVAL_MS));
    }

    setBackendStatus("offline");
  }, []);

  /** Load a grid-based scenario: initialises backend + broadcasts step 0, does NOT start the loop */
  const loadConfig = useCallback(
    async (scenario: GridScenarioConfig, agents?: SimulationAgentsConfig) => {
      try {
        const response = await fetch(`${BACKEND_URL}/api/simulation/load`, {
          method: "POST",
          headers: SESSION_HEADERS,
          body: JSON.stringify({ scenario, agents: agents ?? null }),
        });
        if (!response.ok) {
          const error = await response.json();
          throw new Error(error.detail || "Failed to load simulation");
        }
        const result = await response.json();
        console.log("Simulation loaded:", result);
        setIsLoaded(true);
        setIsRunning(false);
        setIsPaused(false);
        return result;
      } catch (error) {
        console.error("Error loading simulation:", error);
        throw error;
      }
    },
    [],
  );

  /** Start the simulation loop (requires prior call to loadConfig) */
  const startSimulation = useCallback(async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/api/simulation/start`, {
        method: "POST",
        headers: { "X-Session-ID": SESSION_ID },
      });
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || "Failed to start simulation");
      }
      const result = await response.json();
      console.log("Simulation started:", result);
      setIsRunning(true);
      return result;
    } catch (error) {
      console.error("Error starting simulation:", error);
      throw error;
    }
  }, []);

  const uploadConfig = useCallback(async (file: File) => {
    try {
      const formData = new FormData();
      formData.append("file", file);

      const response = await fetch(`${BACKEND_URL}/api/simulation/upload`, {
        method: "POST",
        headers: { "X-Session-ID": SESSION_ID },
        body: formData,
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || "Failed to upload configuration");
      }

      const result = await response.json();
      console.log("Configuration loaded from file:", result);
      setIsLoaded(true);
      setIsRunning(false);
      setIsPaused(false);
      return result;
    } catch (error) {
      console.error("Error uploading configuration:", error);
      throw error;
    }
  }, []);

  const pauseSimulation = useCallback(async () => {
    try {
      await fetch(`${BACKEND_URL}/api/simulation/pause`, {
        method: "POST",
        headers: { "X-Session-ID": SESSION_ID },
      });
    } catch (error) {
      console.error("Error pausing simulation:", error);
    }
  }, []);

  const resumeSimulation = useCallback(async () => {
    try {
      await fetch(`${BACKEND_URL}/api/simulation/resume`, {
        method: "POST",
        headers: { "X-Session-ID": SESSION_ID },
      });
    } catch (error) {
      console.error("Error resuming simulation:", error);
    }
  }, []);

  const stopSimulation = useCallback(async () => {
    try {
      await fetch(`${BACKEND_URL}/api/simulation/stop`, {
        method: "POST",
        headers: { "X-Session-ID": SESSION_ID },
      });
      setIsRunning(false);
    } catch (error) {
      console.error("Error stopping simulation:", error);
    }
  }, []);

  const resetSimulation = useCallback(async () => {
    try {
      await fetch(`${BACKEND_URL}/api/simulation/reset`, {
        method: "POST",
        headers: { "X-Session-ID": SESSION_ID },
      });
      // The backend broadcasts step 0 state + simulation_reset event;
      // we let the socket handlers update isLoaded/isRunning.
    } catch (error) {
      console.error("Error resetting simulation:", error);
    }
  }, []);

  const setSimulationSpeed = useCallback(async (speed: number) => {
    try {
      const response = await fetch(
        `${BACKEND_URL}/api/simulation/speed?speed=${speed}`,
        {
          method: "POST",
          headers: { "X-Session-ID": SESSION_ID },
        },
      );
      if (!response.ok) {
        throw new Error("Failed to set simulation speed");
      }
      console.log(`Simulation speed set to ${speed}x`);
    } catch (error) {
      console.error("Error setting simulation speed:", error);
    }
  }, []);

  return {
    state,
    connected,
    isRunning,
    isPaused,
    isLoaded,
    backendStatus,
    wakeBackend,
    loadConfig,
    startSimulation,
    uploadConfig,
    pauseSimulation,
    resumeSimulation,
    stopSimulation,
    resetSimulation,
    setSimulationSpeed,
  };
};
