"""
CV (Curriculum Vitae) tools for document processing and analysis.

This module provides comprehensive CV processing capabilities including:
- Google Drive document reading
- PDF text extraction  
- CV content analysis
- Skills extraction
- Experience parsing
- Contact information extraction
"""

import logging
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


def create_cv_tools_from_config(config: Dict[str, Any]) -> List[Any]:
    """
    Create CV tools from agent configuration.
    
    Args:
        config: Agent configuration dictionary
        
    Returns:
        List of CV tools
    """
    tools = []
    agent_name = config.get('name', 'unknown')
    
    try:
        # Import Google Drive tools
        from .google_drive_tools import (
            list_files_in_folder,
            read_google_doc,
            read_google_doc_by_name,
            search_files,
            get_file_metadata,
            find_by_name
        )
        
        # Import analysis tools
        from .cv_analyzer_tools import (
            read_and_analyze_cv,
            read_and_analyze_cv_by_name,
            analyze_cv_content,
            analyze_cv_by_name
        )
        
        # Add all CV tools
        tools.extend([
            # Google Drive tools
            list_files_in_folder,
            read_google_doc,
            read_google_doc_by_name,
            search_files,
            get_file_metadata,
            find_by_name,
            # Analysis tools
            read_and_analyze_cv,
            read_and_analyze_cv_by_name,
            analyze_cv_content,
            analyze_cv_by_name
        ])
        
        logger.info(f"Created {len(tools)} CV tools for {agent_name}")
        
    except ImportError as e:
        logger.warning(f"CV tools not available for {agent_name}: {e}")
    except Exception as e:
        logger.error(f"Failed to create CV tools for {agent_name}: {e}")
    
    return tools


def get_available_cv_tools() -> List[str]:
    """
    Get list of available CV tool names.
    
    Returns:
        List of CV tool names
    """
    return [
        'list_files_in_folder',
        'read_google_doc',
        'read_google_doc_by_name', 
        'search_files',
        'get_file_metadata',
        'find_by_name',
        'read_and_analyze_cv',
        'read_and_analyze_cv_by_name',
        'analyze_cv_content',
        'analyze_cv_by_name'
    ]
