// Main App Component

import React from "react";
import { useSimulation } from "./hooks/useSimulation";
import { GridCanvas } from "./components/GridCanvas";
import { ControlPanel } from "./components/ControlPanel";
import { MetricsDisplay } from "./components/MetricsDisplay";
import "./index.css";

function App() {
  const {
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
  } = useSimulation();

  return (
    <div className="min-h-screen bg-gray-900 text-white p-6">
      {/* Header */}
      <header className="mb-8">
        <h1 className="text-4xl font-bold mb-2">
          Warehouse Swarm Intelligence System
        </h1>
        <p className="text-gray-400">
          Multi-Agent Object Retrieval Simulation with Coordinated Exploration
        </p>
      </header>

      {/* Main Content */}
      <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
        {/* Left Sidebar - Controls */}
        <div className="xl:col-span-1">
          <ControlPanel
            connected={connected}
            isRunning={isRunning}
            isPaused={isPaused}
            onStart={startSimulation}
            onUpload={uploadConfig}
            onPause={pauseSimulation}
            onResume={resumeSimulation}
            onStop={stopSimulation}
            onReset={resetSimulation}
          />
        </div>

        {/* Center - Visualization */}
        <div className="xl:col-span-2">
          <div className="bg-gray-800 rounded-lg p-6">
            <h2 className="text-2xl font-bold mb-4">Simulation View</h2>
            {state && state.grid ? (
              <GridCanvas state={state} width={800} height={800} />
            ) : (
              <div className="flex items-center justify-center h-96 border border-gray-700 rounded-lg bg-gray-900">
                <div className="text-center">
                  <svg
                    className="mx-auto h-12 w-12 text-gray-600 mb-4"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"
                    />
                  </svg>
                  <h3 className="text-xl font-medium text-gray-400 mb-2">
                    No Simulation Running
                  </h3>
                  <p className="text-gray-500">Load a configuration to start</p>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Right Sidebar - Metrics */}
        <div className="xl:col-span-1">
          <MetricsDisplay state={state} />
        </div>
      </div>

      {/* Footer */}
      <footer className="mt-8 text-center text-gray-500 text-sm">
        <p className="mb-1">
          Multi-Agent Systems Project - Swarm Intelligence for Warehouse
          Management
        </p>
        <p>© 2026 - Built with React, FastAPI, Mesa</p>
      </footer>
    </div>
  );
}

export default App;
