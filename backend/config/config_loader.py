"""
Configuration loader for JSON scenario files
"""

import json
from pathlib import Path
from typing import Union

from backend.config.schemas import GridScenarioConfig, ScenarioConfig


class ConfigLoader:
    """Load and validate scenario configurations from JSON"""

    @staticmethod
    def load_from_file(file_path: Union[str, Path]) -> ScenarioConfig:
        """
        Load configuration from JSON file

        Args:
            file_path: Path to JSON configuration file

        Returns:
            Validated ScenarioConfig object

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If JSON is invalid or doesn't match schema
        """
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return ConfigLoader.load_from_dict(data)

    @staticmethod
    def load_from_dict(data: dict) -> ScenarioConfig:
        """
        Load configuration from dictionary

        Args:
            data: Configuration dictionary

        Returns:
            Validated ScenarioConfig object

        Raises:
            ValueError: If data doesn't match schema
        """
        try:
            return ScenarioConfig(**data)
        except Exception as e:
            raise ValueError(f"Invalid configuration: {e}")

    @staticmethod
    def save_to_file(config: ScenarioConfig, file_path: Union[str, Path]) -> None:
        """
        Save configuration to JSON file

        Args:
            config: ScenarioConfig object to save
            file_path: Destination file path
        """
        file_path = Path(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(config.model_dump(), f, indent=2)


class GridConfigLoader:
    """Load and validate compact grid-based scenario configurations (A/B format)"""

    @staticmethod
    def load_from_file(file_path: Union[str, Path]) -> GridScenarioConfig:
        """
        Load a grid-based scenario from a JSON file.

        Args:
            file_path: Path to JSON file in A/B format.

        Returns:
            Validated GridScenarioConfig object.

        Raises:
            FileNotFoundError: If file doesn't exist.
            ValueError: If JSON is invalid or doesn't match schema.
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return GridConfigLoader.load_from_dict(data)

    @staticmethod
    def load_from_dict(data: dict) -> GridScenarioConfig:
        """
        Load a grid-based scenario from a dictionary.

        Args:
            data: Raw dictionary (must have ``metadata``, ``grid``,
                  ``warehouses``, ``objects`` keys).

        Returns:
            Validated GridScenarioConfig object.

        Raises:
            ValueError: If data doesn't match schema.
        """
        try:
            return GridScenarioConfig(**data)
        except Exception as e:
            raise ValueError(f"Invalid grid configuration: {e}")
