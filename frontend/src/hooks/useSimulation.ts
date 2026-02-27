// Custom hook for managing simulation WebSocket connection

import { useState, useEffect, useCallback, useRef } from "react";
import { io, Socket } from "socket.io-client";
import { SimulationState, SimulationConfig } from "../types/simulation";

const BACKEND_URL = "http://localhost:8000";

export const useSimulation = () => {
  const [state, setState] = useState<SimulationState | null>(null);
  const [connected, setConnected] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const socketRef = useRef<Socket | null>(null);

  useEffect(() => {
    // Connect to WebSocket
    const socket = io(BACKEND_URL, {
      transports: ["websocket", "polling"],
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
      setState(null);
      setIsRunning(false);
      setIsPaused(false);
    });

    return () => {
      socket.disconnect();
    };
  }, []);

  const startSimulation = useCallback(async (config: SimulationConfig) => {
    try {
      const response = await fetch(`${BACKEND_URL}/api/simulation/start`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ config }),
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
        body: formData,
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || "Failed to upload configuration");
      }

      const result = await response.json();
      console.log("Configuration uploaded:", result);
      setIsRunning(true);
      return result;
    } catch (error) {
      console.error("Error uploading configuration:", error);
      throw error;
    }
  }, []);

  const pauseSimulation = useCallback(async () => {
    try {
      await fetch(`${BACKEND_URL}/api/simulation/pause`, { method: "POST" });
    } catch (error) {
      console.error("Error pausing simulation:", error);
    }
  }, []);

  const resumeSimulation = useCallback(async () => {
    try {
      await fetch(`${BACKEND_URL}/api/simulation/resume`, { method: "POST" });
    } catch (error) {
      console.error("Error resuming simulation:", error);
    }
  }, []);

  const stopSimulation = useCallback(async () => {
    try {
      await fetch(`${BACKEND_URL}/api/simulation/stop`, { method: "POST" });
      setIsRunning(false);
    } catch (error) {
      console.error("Error stopping simulation:", error);
    }
  }, []);

  const resetSimulation = useCallback(async () => {
    try {
      await fetch(`${BACKEND_URL}/api/simulation/reset`, { method: "POST" });
      setState(null);
    } catch (error) {
      console.error("Error resetting simulation:", error);
    }
  }, []);

  return {
    state,
    connected,
    isRunning,
    isPaused,
    startSimulation,
    uploadConfig,
    pauseSimulation,
    resumeSimulation,
    stopSimulation,
    resetSimulation,
  };
};
