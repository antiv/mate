"""
CV Analyzer tools for content analysis and processing.

This module provides tool functions that combine Google Drive document reading
with CV content analysis capabilities.
"""

import logging
from typing import Dict, Any
from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)


def read_and_analyze_cv(doc_id: str, analysis_type: str, tool_context: ToolContext) -> Dict[str, Any]:
    """Read CV from Google Drive and analyze it."""
    print(f"--- Tool: read_and_analyze_cv called for doc_id: {doc_id}, analysis_type: {analysis_type} ---")
    
    try:
        # Import Google Drive tools
        from .google_drive_tools import read_google_doc
        from .cv_analyzer import analyze_cv_content
        
        # First read the document
        read_result = read_google_doc(doc_id, tool_context)
        
        if read_result["status"] == "error":
            return read_result
        
        # Then analyze the content
        content = read_result["content"]
        analysis_result = analyze_cv_content(doc_id, content, analysis_type, tool_context)
        
        # Combine results
        result = {
            "status": "success",
            "doc_id": doc_id,
            "doc_name": read_result.get("doc_name", "Unknown"),
            "analysis_type": analysis_type,
            "content_preview": content[:500] + "..." if len(content) > 500 else content,
            "analysis": analysis_result.get("analysis", {})
        }
        
        print(f"--- Tool: Completed read and analysis for {read_result.get('doc_name', 'Unknown')} ---")
        return result
        
    except Exception as e:
        logger.error(f"Error in read_and_analyze_cv: {e}")
        return {
            "status": "error",
            "error": f"Failed to read and analyze CV: {str(e)}"
        }


def read_and_analyze_cv_by_name(doc_name: str, folder_id: str, analysis_type: str, tool_context: ToolContext) -> Dict[str, Any]:
    """Read CV from Google Drive by name and analyze it."""
    print(f"--- Tool: read_and_analyze_cv_by_name called for doc_name: {doc_name}, folder_id: {folder_id}, analysis_type: {analysis_type} ---")
    
    try:
        # Import Google Drive tools and analyzer
        from .google_drive_tools import read_google_doc_by_name
        from .cv_analyzer import analyze_cv_by_name
        
        # First read the document by name
        # Handle empty folder_id by using environment variable
        if not folder_id:
            import os
            folder_id = os.getenv('GOOGLE_DRIVE_FOLDER_ID', '')
        read_result = read_google_doc_by_name(doc_name, folder_id, tool_context)
        
        if read_result["status"] == "error":
            return read_result
        
        # Then analyze the content
        content = read_result["content"]
        analysis_result = analyze_cv_by_name(doc_name, folder_id, content, analysis_type, tool_context)
        
        # Combine results
        result = {
            "status": "success",
            "doc_name": doc_name,
            "doc_id": read_result.get("doc_id", "Unknown"),
            "analysis_type": analysis_type,
            "content_preview": content[:500] + "..." if len(content) > 500 else content,
            "analysis": analysis_result.get("analysis", {})
        }
        
        print(f"--- Tool: Completed read and analysis for {read_result.get('doc_name', 'Unknown')} ---")
        return result
        
    except Exception as e:
        logger.error(f"Error in read_and_analyze_cv_by_name: {e}")
        return {
            "status": "error",
            "error": f"Failed to read and analyze CV by name: {str(e)}"
        }


def analyze_cv_content(doc_id: str, content: str, analysis_type: str, tool_context: ToolContext) -> Dict[str, Any]:
    """Analyze CV content and return structured analysis."""
    print(f"--- Tool: analyze_cv_content called for doc_id: {doc_id}, analysis_type: {analysis_type} ---")
    
    try:
        from .cv_analyzer import CVAnalyzer
        
        analyzer = CVAnalyzer()
        
        if analysis_type == "comprehensive":
            analysis = analyzer.comprehensive_analysis(content)
        elif analysis_type == "skills":
            skills = analyzer.extract_skills(content)
            analysis = {"skills": skills}
        elif analysis_type == "experience":
            experience = analyzer.extract_experience(content)
            analysis = {"experience": experience}
        elif analysis_type == "contact":
            contact = analyzer.extract_contact_info(content)
            analysis = {"contact_info": contact}
        elif analysis_type == "education":
            education = analyzer.extract_education(content)
            analysis = {"education": education}
        else:
            # Default to comprehensive
            analysis = analyzer.comprehensive_analysis(content)
        
        result = {
            "status": "success",
            "doc_id": doc_id,
            "analysis_type": analysis_type,
            "analysis": analysis
        }
        
        print(f"--- Tool: Completed analysis for doc_id: {doc_id} ---")
        return result
        
    except Exception as e:
        logger.error(f"Error in analyze_cv_content: {e}")
        return {
            "status": "error",
            "error": f"Failed to analyze CV content: {str(e)}"
        }


def analyze_cv_by_name(doc_name: str, folder_id: str, content: str, analysis_type: str, tool_context: ToolContext) -> Dict[str, Any]:
    """Analyze CV content by document name."""
    print(f"--- Tool: analyze_cv_by_name called for doc_name: {doc_name}, analysis_type: {analysis_type} ---")
    
    try:
        from .cv_analyzer import CVAnalyzer
        
        analyzer = CVAnalyzer()
        
        if analysis_type == "comprehensive":
            analysis = analyzer.comprehensive_analysis(content)
        elif analysis_type == "skills":
            skills = analyzer.extract_skills(content)
            analysis = {"skills": skills}
        elif analysis_type == "experience":
            experience = analyzer.extract_experience(content)
            analysis = {"experience": experience}
        elif analysis_type == "contact":
            contact = analyzer.extract_contact_info(content)
            analysis = {"contact_info": contact}
        elif analysis_type == "education":
            education = analyzer.extract_education(content)
            analysis = {"education": education}
        else:
            # Default to comprehensive
            analysis = analyzer.comprehensive_analysis(content)
        
        result = {
            "status": "success",
            "doc_name": doc_name,
            "folder_id": folder_id,
            "analysis_type": analysis_type,
            "analysis": analysis
        }
        
        print(f"--- Tool: Completed analysis for doc_name: {doc_name} ---")
        return result
        
    except Exception as e:
        logger.error(f"Error in analyze_cv_by_name: {e}")
        return {
            "status": "error",
            "error": f"Failed to analyze CV by name: {str(e)}"
        }
