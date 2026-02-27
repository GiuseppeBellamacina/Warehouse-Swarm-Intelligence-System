"""
Agent communication and map sharing system
"""

from typing import Dict, List, Set, Tuple, Optional
from dataclasses import dataclass
import numpy as np


@dataclass
class Message:
    """Base message class for agent communication"""
    sender_id: int
    timestamp: int


@dataclass
class MapDataMessage(Message):
    """Message containing explored map data"""
    explored_cells: List[Tuple[int, int, int]]  # (x, y, cell_type)
    
    
@dataclass
class ObjectLocationMessage(Message):
    """Message about discovered object location"""
    object_position: Tuple[int, int]
    object_value: float = 1.0


@dataclass
class TaskAssignmentMessage(Message):
    """Task assignment from coordinator to retriever"""
    target_id: int  # Receiver agent ID
    task_type: str  # "retrieve", "explore", "assist"
    target_position: Optional[Tuple[int, int]] = None
    priority: float = 1.0


@dataclass
class StatusMessage(Message):
    """Agent status update"""
    agent_id: int
    position: Tuple[int, int]
    energy_level: float
    state: str
    carrying_objects: int = 0


class CommunicationManager:
    """
    Manages agent-to-agent communication via proximity-based message passing
    """
    
    def __init__(self):
        self.message_queue: List[Message] = []
        self.agent_mailboxes: Dict[int, List[Message]] = {}
        
    def send_message(self, message: Message, recipients: List[int]) -> None:
        """
        Send a message to specific recipients
        
        Args:
            message: Message to send
            recipients: List of agent IDs to receive the message
        """
        for recipient_id in recipients:
            if recipient_id not in self.agent_mailboxes:
                self.agent_mailboxes[recipient_id] = []
            self.agent_mailboxes[recipient_id].append(message)
    
    def broadcast_in_radius(
        self, 
        message: Message, 
        sender_pos: Tuple[int, int],
        all_agent_positions: Dict[int, Tuple[int, int]],
        communication_radius: float
    ) -> int:
        """
        Broadcast message to all agents within communication radius
        
        Args:
            message: Message to broadcast
            sender_pos: Position of sender
            all_agent_positions: Dictionary of agent_id -> position
            communication_radius: Communication range
            
        Returns:
            Number of agents that received the message
        """
        recipients = []
        sender_x, sender_y = sender_pos
        radius_sq = communication_radius ** 2
        
        for agent_id, (x, y) in all_agent_positions.items():
            if agent_id == message.sender_id:
                continue  # Don't send to self
            
            dist_sq = (x - sender_x) ** 2 + (y - sender_y) ** 2
            if dist_sq <= radius_sq:
                recipients.append(agent_id)
        
        self.send_message(message, recipients)
        return len(recipients)
    
    def get_messages(self, agent_id: int) -> List[Message]:
        """
        Retrieve all messages for an agent and clear mailbox
        
        Args:
            agent_id: Agent ID
            
        Returns:
            List of messages for this agent
        """
        messages = self.agent_mailboxes.get(agent_id, [])
        self.agent_mailboxes[agent_id] = []
        return messages
    
    def clear_all(self) -> None:
        """Clear all mailboxes"""
        self.message_queue.clear()
        self.agent_mailboxes.clear()


class MapSharingSystem:
    """
    Handles merging and sharing of local exploration maps between agents
    """
    
    @staticmethod
    def merge_maps(map_a: np.ndarray, map_b: np.ndarray) -> np.ndarray:
        """
        Merge two exploration maps, preserving known information
        
        Strategy:
        - If cell is UNKNOWN in map_a but known in map_b, use map_b value
        - Otherwise keep map_a value
        
        Args:
            map_a: Primary map (will be updated)
            map_b: Secondary map (source of new information)
            
        Returns:
            Merged map
        """
        merged = map_a.copy()
        
        # Update UNKNOWN cells with known information from map_b
        unknown_mask = (map_a == 0)  # CellType.UNKNOWN = 0
        merged[unknown_mask] = map_b[unknown_mask]
        
        return merged
    
    @staticmethod
    def extract_explored_cells(
        local_map: np.ndarray
    ) -> List[Tuple[int, int, int]]:
        """
        Extract all explored (non-UNKNOWN) cells from a map
        
        Args:
            local_map: Agent's local exploration map
            
        Returns:
            List of (x, y, cell_type) tuples for explored cells
        """
        explored = []
        height, width = local_map.shape
        
        for y in range(height):
            for x in range(width):
                cell_type = local_map[y, x]
                if cell_type != 0:  # Not UNKNOWN
                    explored.append((x, y, int(cell_type)))
        
        return explored
    
    @staticmethod
    def apply_shared_map_data(
        local_map: np.ndarray,
        shared_cells: List[Tuple[int, int, int]]
    ) -> np.ndarray:
        """
        Apply shared map data to local map
        
        Args:
            local_map: Agent's local map
            shared_cells: List of (x, y, cell_type) from other agent
            
        Returns:
            Updated local map
        """
        updated = local_map.copy()
        height, width = updated.shape
        
        for x, y, cell_type in shared_cells:
            if 0 <= x < width and 0 <= y < height:
                # Only update if currently UNKNOWN
                if updated[y, x] == 0:
                    updated[y, x] = cell_type
        
        return updated


class CoordinationSystem:
    """
    Coordinates task assignments between coordinator and retriever agents
    """
    
    def __init__(self):
        self.known_objects: Dict[Tuple[int, int], float] = {}  # position -> value
        self.assigned_tasks: Dict[int, Tuple[int, int]] = {}  # agent_id -> target_pos
        self.completed_tasks: Set[Tuple[int, int]] = set()
        
    def register_object(self, position: Tuple[int, int], value: float = 1.0) -> None:
        """Register a discovered object"""
        if position not in self.completed_tasks:
            self.known_objects[position] = value
    
    def assign_task(
        self, 
        retriever_id: int, 
        target_position: Tuple[int, int]
    ) -> bool:
        """
        Assign a retrieval task to an agent
        
        Args:
            retriever_id: ID of retriever agent
            target_position: Object position to retrieve
            
        Returns:
            True if assignment successful
        """
        if target_position in self.known_objects:
            self.assigned_tasks[retriever_id] = target_position
            # Remove from known objects to avoid double assignment
            del self.known_objects[target_position]
            return True
        return False
    
    def complete_task(self, retriever_id: int) -> None:
        """Mark a task as completed"""
        if retriever_id in self.assigned_tasks:
            target = self.assigned_tasks[retriever_id]
            self.completed_tasks.add(target)
            del self.assigned_tasks[retriever_id]
    
    def get_unassigned_objects(self) -> List[Tuple[int, int]]:
        """Get list of known but unassigned object positions"""
        return list(self.known_objects.keys())
    
    def get_assignment(self, retriever_id: int) -> Optional[Tuple[int, int]]:
        """Get current assignment for a retriever"""
        return self.assigned_tasks.get(retriever_id)
