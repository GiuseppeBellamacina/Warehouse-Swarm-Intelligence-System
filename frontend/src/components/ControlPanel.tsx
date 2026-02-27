// Control Panel Component

import React, { useState, useRef } from "react";
import { SimulationConfig } from "../types/simulation";

interface ControlPanelProps {
  connected: boolean;
  isRunning: boolean;
  isPaused: boolean;
  onStart: (config: SimulationConfig) => void;
  onUpload: (file: File) => void;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
  onReset: () => void;
}

export const ControlPanel: React.FC<ControlPanelProps> = ({
  connected,
  isRunning,
  isPaused,
  onStart,
  onUpload,
  onPause,
  onResume,
  onStop,
  onReset,
}) => {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [configName, setConfigName] = useState<string>("simple_scenario");
  const [availableConfigs, setAvailableConfigs] = useState<string[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Load available configs on mount
  React.useEffect(() => {
    const loadConfigs = async () => {
      try {
        const response = await fetch("http://localhost:8000/api/configs");
        if (response.ok) {
          const data = await response.json();
          setAvailableConfigs(data.configs || []);
          // Set default if available
          if (data.configs && data.configs.length > 0) {
            setConfigName(data.configs[0]);
          }
        }
      } catch (error) {
        console.error("Error loading configs:", error);
      }
    };
    loadConfigs();
  }, []);

  const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) {
      setSelectedFile(file);
    }
  };

  const handleUpload = () => {
    if (selectedFile) {
      onUpload(selectedFile);
    }
  };

  const handleLoadExample = async () => {
    try {
      // Load directly from backend
      const response = await fetch(
        `http://localhost:8000/configs/${configName}.json`,
      );

      if (!response.ok) {
        throw new Error(`Config file not found: ${response.status}`);
      }

      // Verify content type is JSON
      const contentType = response.headers.get("content-type");
      if (!contentType || !contentType.includes("application/json")) {
        throw new Error(`Expected JSON but got: ${contentType}`);
      }

      const config = await response.json();
      onStart(config);
    } catch (error) {
      console.error("Error loading example:", error);
      alert(
        `Failed to load example configuration: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  };

  return (
    <div className="bg-gray-800 rounded-lg p-6 space-y-6">
      <div>
        <h2 className="text-2xl font-bold mb-4">Control Panel</h2>

        {/* Connection Status */}
        <div className="flex items-center mb-4">
          <div
            className={`h-3 w-3 rounded-full mr-2 ${connected ? "bg-green-500" : "bg-red-500"}`}
          />
          <span className="text-sm">
            {connected ? "Connected" : "Disconnected"}
          </span>
        </div>
      </div>

      {/* Configuration Upload */}
      <div className="border-t border-gray-700 pt-4">
        <h3 className="text-lg font-semibold mb-3">Load Configuration</h3>

        {/* Example Configs */}
        <div className="mb-4">
          <label className="block text-sm font-medium mb-2">
            Example Scenarios
          </label>
          <div className="flex gap-2">
            <select
              value={configName}
              onChange={(e) => setConfigName(e.target.value)}
              className="flex-1 bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white"
              disabled={isRunning}
            >
              {availableConfigs.length > 0 ? (
                availableConfigs.map((config) => (
                  <option key={config} value={config}>
                    {config
                      .replace(/_/g, " ")
                      .replace(/\b\w/g, (l) => l.toUpperCase())}
                  </option>
                ))
              ) : (
                <option value="">Loading...</option>
              )}
            </select>
            <button
              onClick={handleLoadExample}
              disabled={isRunning || !connected || !configName}
              className="bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 disabled:cursor-not-allowed px-4 py-2 rounded font-medium transition"
            >
              Load
            </button>
          </div>
        </div>

        {/* File Upload */}
        <div>
          <label className="block text-sm font-medium mb-2">
            Or Upload JSON
          </label>
          <div className="flex gap-2">
            <input
              ref={fileInputRef}
              type="file"
              accept=".json"
              onChange={handleFileSelect}
              className="hidden"
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={isRunning}
              className="flex-1 bg-gray-700 hover:bg-gray-600 disabled:bg-gray-600 disabled:cursor-not-allowed px-3 py-2 rounded border border-gray-600 transition"
            >
              {selectedFile ? selectedFile.name : "Choose File"}
            </button>
            <button
              onClick={handleUpload}
              disabled={!selectedFile || isRunning || !connected}
              className="bg-green-600 hover:bg-green-700 disabled:bg-gray-600 disabled:cursor-not-allowed px-4 py-2 rounded font-medium transition"
            >
              Upload & Start
            </button>
          </div>
        </div>
      </div>

      {/* Simulation Controls */}
      <div className="border-t border-gray-700 pt-4">
        <h3 className="text-lg font-semibold mb-3">Simulation Controls</h3>
        <div className="grid grid-cols-2 gap-2">
          {!isRunning || isPaused ? (
            <button
              onClick={isPaused ? onResume : () => {}}
              disabled={!isPaused || !connected}
              className="bg-green-600 hover:bg-green-700 disabled:bg-gray-600 disabled:cursor-not-allowed px-4 py-2 rounded font-medium transition"
            >
              {isPaused ? "Resume" : "Start"}
            </button>
          ) : (
            <button
              onClick={onPause}
              disabled={!isRunning || isPaused || !connected}
              className="bg-yellow-600 hover:bg-yellow-700 disabled:bg-gray-600 disabled:cursor-not-allowed px-4 py-2 rounded font-medium transition"
            >
              Pause
            </button>
          )}

          <button
            onClick={onStop}
            disabled={!isRunning || !connected}
            className="bg-red-600 hover:bg-red-700 disabled:bg-gray-600 disabled:cursor-not-allowed px-4 py-2 rounded font-medium transition"
          >
            Stop
          </button>

          <button
            onClick={onReset}
            disabled={!connected}
            className="col-span-2 bg-gray-600 hover:bg-gray-700 disabled:bg-gray-600 disabled:cursor-not-allowed px-4 py-2 rounded font-medium transition"
          >
            Reset
          </button>
        </div>
      </div>

      {/* Status */}
      {isRunning && (
        <div className="border-t border-gray-700 pt-4">
          <div className="bg-gray-700 rounded p-3 text-center">
            <span className="text-green-400 font-medium">
              {isPaused ? "⏸ PAUSED" : "▶ RUNNING"}
            </span>
          </div>
        </div>
      )}
    </div>
  );
};
