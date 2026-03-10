// Timeline slider for step-by-step replay

import React, { useCallback } from "react";

interface TimelineSliderProps {
  /** Minimum step number available */
  minStep: number;
  /** Maximum step number available */
  maxStep: number;
  /** Total snapshots stored */
  count: number;
  /** Currently viewed step (null = live) */
  viewStep: number | null;
  /** Set the viewed step (null = back to live) */
  onChangeStep: (step: number | null) => void;
  /** Current live step for display */
  liveStep: number;
  /** Whether simulation is currently running */
  isRunning: boolean;
}

export const TimelineSlider: React.FC<TimelineSliderProps> = ({
  minStep,
  maxStep,
  count,
  viewStep,
  onChangeStep,
  liveStep,
  isRunning,
}) => {
  const isLive = viewStep === null;
  const displayStep = isLive ? liveStep : viewStep;

  const handleSliderChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const v = parseInt(e.target.value, 10);
      // If dragged to the max and simulation running, snap to live
      if (v >= maxStep && isRunning) {
        onChangeStep(null);
      } else {
        onChangeStep(v);
      }
    },
    [maxStep, isRunning, onChangeStep],
  );

  const handleGoLive = useCallback(() => {
    onChangeStep(null);
  }, [onChangeStep]);

  const handleStepBack = useCallback(() => {
    const cur = isLive ? maxStep : viewStep!;
    const next = Math.max(minStep, cur - 1);
    onChangeStep(next);
  }, [isLive, maxStep, viewStep, minStep, onChangeStep]);

  const handleStepForward = useCallback(() => {
    if (isLive) return;
    const next = viewStep! + 1;
    if (next >= maxStep && isRunning) {
      onChangeStep(null);
    } else {
      onChangeStep(Math.min(maxStep, next));
    }
  }, [isLive, viewStep, maxStep, isRunning, onChangeStep]);

  if (count < 2) return null;

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 bg-gray-900/80 border-t border-gray-800/60 backdrop-blur-sm">
      {/* Step back */}
      <button
        onClick={handleStepBack}
        className="p-1 rounded hover:bg-gray-700/60 text-gray-400 hover:text-gray-200 transition-colors"
        title="Previous step"
      >
        <svg
          className="w-3.5 h-3.5"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth="2"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M15.75 19.5L8.25 12l7.5-7.5"
          />
        </svg>
      </button>

      {/* Step forward */}
      <button
        onClick={handleStepForward}
        disabled={isLive}
        className="p-1 rounded hover:bg-gray-700/60 text-gray-400 hover:text-gray-200 disabled:opacity-30 transition-colors"
        title="Next step"
      >
        <svg
          className="w-3.5 h-3.5"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth="2"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M8.25 4.5l7.5 7.5-7.5 7.5"
          />
        </svg>
      </button>

      {/* Step label */}
      <span className="text-[10px] font-mono text-gray-500 w-16 text-center tabular-nums select-none">
        {displayStep} / {maxStep}
      </span>

      {/* Slider */}
      <input
        type="range"
        min={minStep}
        max={maxStep}
        value={displayStep}
        onChange={handleSliderChange}
        className="flex-1 h-1 accent-blue-500 cursor-pointer appearance-none bg-gray-700/60 rounded-full
          [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3
          [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-blue-500 [&::-webkit-slider-thumb]:shadow-md
          [&::-webkit-slider-thumb]:cursor-pointer
          [&::-moz-range-thumb]:w-3 [&::-moz-range-thumb]:h-3 [&::-moz-range-thumb]:rounded-full
          [&::-moz-range-thumb]:bg-blue-500 [&::-moz-range-thumb]:border-0 [&::-moz-range-thumb]:cursor-pointer"
      />

      {/* Live button */}
      <button
        onClick={handleGoLive}
        className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border transition-all ${
          isLive
            ? "bg-red-600/80 border-red-500/60 text-white"
            : "bg-gray-800/60 border-gray-700/50 text-gray-400 hover:text-white hover:border-red-500/60 hover:bg-red-900/40"
        }`}
        title="Return to live view"
      >
        ● LIVE
      </button>

      {/* Snapshot count */}
      <span
        className="text-[9px] text-gray-600 tabular-nums select-none"
        title="Snapshots in memory"
      >
        {count}
      </span>
    </div>
  );
};
