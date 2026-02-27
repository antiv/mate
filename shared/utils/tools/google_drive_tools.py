import os
import io
from typing import Dict, Any, List, Optional
from google.adk.tools.tool_context import ToolContext
from google.auth import default
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import base64
import mimetypes
import PyPDF2

# Service Account scopes for Google Drive API
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

def extract_text_from_pdf(pdf_content: bytes) -> str:
    """Extract text content from PDF bytes."""
    try:
        # Create a BytesIO object from the PDF content
        pdf_file = io.BytesIO(pdf_content)
        
        # Create PDF reader object
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        
        # Extract text from all pages
        text_content = []
        for page_num in range(len(pdf_reader.pages)):
            page = pdf_reader.pages[page_num]
            text_content.append(page.extract_text())
        
        # Join all text content
        full_text = '\n'.join(text_content)
        
        # Clean up the text (remove excessive whitespace)
        cleaned_text = ' '.join(full_text.split())
        
        return cleaned_text if cleaned_text else "[No text content found in PDF]"
        
    except Exception as e:
        return f"[Error extracting PDF text: {str(e)}]"

def get_google_drive_service():
    """Get authenticated Google Drive service using Service Account."""
    try:
        # First try to use service account credentials from environment variable
        service_account_info = os.getenv('GOOGLE_SERVICE_ACCOUNT_INFO')
        
        if service_account_info:
            # Parse JSON from environment variable
            import json
            credentials_info = json.loads(service_account_info)
            credentials = service_account.Credentials.from_service_account_info(
                credentials_info, scopes=SCOPES
            )
        else:
            # Try to load from service account key file
            service_account_file = os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE', 'service-account-key.json')
            
            if os.path.exists(service_account_file):
                credentials = service_account.Credentials.from_service_account_file(
                    service_account_file, scopes=SCOPES
                )
            else:
                # Fallback to default credentials (useful for GCP environments)
                credentials, project = default(scopes=SCOPES)
        
        return build('drive', 'v3', credentials=credentials)
        
    except Exception as e:
        raise Exception(f"Failed to authenticate with Google Drive: {str(e)}")

def list_files_in_folder(folder_id: str, tool_context: ToolContext) -> Dict[str, Any]:
    """List all files in a Google Drive folder. If folder_id is empty, uses GOOGLE_DRIVE_FOLDER_ID environment variable."""
    try:
        service = get_google_drive_service()

        # if folder_id is empty, use the default folder from environment variable
        if not folder_id:
            folder_id = os.getenv('GOOGLE_DRIVE_FOLDER_ID')
        
        if not folder_id:
            return {
                "status": "error",
                "error_message": "No folder ID provided and GOOGLE_DRIVE_FOLDER_ID environment variable is not set"
            }
        
        # Query for files in the specified folder
        query = f"'{folder_id}' in parents and (mimeType contains 'application/pdf' or mimeType contains 'application/vnd.google-apps.document' or mimeType contains 'text/plain')"
        
        results = service.files().list(
            q=query,
            pageSize=100,
            fields="nextPageToken, files(id, name, mimeType, size, modifiedTime)"
        ).execute()
        
        files = results.get('files', [])
        
        if not files:
            return {
                "status": "success",
                "message": "No files found in the specified folder",
                "files": []
            }
        
        file_list = []
        for file in files:
            file_info = {
                "id": file['id'],
                "name": file['name'],
                "mimeType": file['mimeType'],
                "size": file.get('size', 'Unknown'),
                "modifiedTime": file['modifiedTime']
            }
            file_list.append(file_info)
        
        return {
            "status": "success",
            "message": f"Found {len(files)} files in the folder",
            "files": file_list
        }
        
    except Exception as e:
        return {
            "status": "error",
            "error_message": f"Failed to list files: {str(e)}"
        }

def read_google_doc(doc_id: str, tool_context: ToolContext) -> Dict[str, Any]:
    """Read content from a Google Doc."""
    try:
        service = get_google_drive_service()
        
        # Get the document content
        document = service.files().get(fileId=doc_id).execute()
        
        # Check the file type and handle accordingly
        mime_type = document['mimeType']
        
        # Google Workspace files - must use export_media
        if mime_type.startswith('application/vnd.google-apps.'):
            # Map Google Workspace mime types to export formats
            export_formats = {
                'application/vnd.google-apps.document': 'text/plain',  # Google Docs
                'application/vnd.google-apps.spreadsheet': 'text/csv',  # Google Sheets
                'application/vnd.google-apps.presentation': 'text/plain',  # Google Slides
            }
            
            export_mime = export_formats.get(mime_type, 'text/plain')
            
            request = service.files().export_media(
                fileId=doc_id,
                mimeType=export_mime
            )
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
            
            content = fh.getvalue().decode('utf-8')
            
        elif mime_type == 'application/pdf':
            # PDF file - download and extract text
            request = service.files().get_media(fileId=doc_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
            
            # Extract text from PDF
            pdf_content = fh.getvalue()
            content = extract_text_from_pdf(pdf_content)
            
        else:
            # Other file types (text, docx, etc.) - download as binary
            request = service.files().get_media(fileId=doc_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
            
            # Try to decode as text, fallback to binary info
            try:
                content = fh.getvalue().decode('utf-8')
            except UnicodeDecodeError:
                content = f"[Binary file content - {mime_type}]"
        
        return {
            "status": "success",
            "doc_id": doc_id,
            "doc_name": document['name'],
            "content": content,
            "mime_type": mime_type,
            "message": f"Successfully retrieved content from {document['name']}"
        }
        
    except Exception as e:
        return {
            "status": "error",
            "doc_id": doc_id,
            "error_message": f"Failed to read document: {str(e)}"
        }

def read_google_doc_by_name(doc_name: str, folder_id: str, tool_context: ToolContext) -> Dict[str, Any]:
    """Read content from a Google Doc by searching for its name in a folder. If folder_id is empty, uses GOOGLE_DRIVE_FOLDER_ID environment variable."""
    try:
        service = get_google_drive_service()

        if not folder_id:
            folder_id = os.getenv('GOOGLE_DRIVE_FOLDER_ID')

        if not folder_id:
            return {
                "status": "error",
                "error_message": "No folder ID provided and GOOGLE_DRIVE_FOLDER_ID environment variable is not set"
            }

        # Search for the file by name in the specified folder
        query = f"name='{doc_name}' and '{folder_id}' in parents"
        
        results = service.files().list(
            q=query,
            fields="files(id, name, mimeType)"
        ).execute()
        
        files = results.get('files', [])
        
        if not files:
            return {
                "status": "error",
                "error_message": f"No file named '{doc_name}' found in the specified folder"
            }
        
        # Use the first matching file
        file_id = files[0]['id']
        return read_google_doc(file_id, tool_context)
        
    except Exception as e:
        return {
            "status": "error",
            "error_message": f"Failed to find document by name: {str(e)}"
        }

def search_files(query: str, folder_id: str, tool_context: ToolContext) -> Dict[str, Any]:
    """Search for files in a Google Drive folder using a query. If folder_id is empty, uses GOOGLE_DRIVE_FOLDER_ID environment variable."""
    try:
        service = get_google_drive_service()
        
        if not folder_id:
            folder_id = os.getenv('GOOGLE_DRIVE_FOLDER_ID')

        if not folder_id:
            return {
                "status": "error",
                "error_message": "No folder ID provided and GOOGLE_DRIVE_FOLDER_ID environment variable is not set"
            }

        # Search for files containing the query in name or content
        search_query = f"'{folder_id}' in parents and (name contains '{query}' or fullText contains '{query}') and (mimeType contains 'application/pdf' or mimeType contains 'application/vnd.google-apps.document' or mimeType contains 'text/plain')"
        
        results = service.files().list(
            q=search_query,
            pageSize=50,
            fields="nextPageToken, files(id, name, mimeType, size, modifiedTime)"
        ).execute()
        
        files = results.get('files', [])
        
        if not files:
            return {
                "status": "success",
                "message": f"No files found matching query: {query}",
                "files": []
            }
        
        file_list = []
        for file in files:
            file_info = {
                "id": file['id'],
                "name": file['name'],
                "mimeType": file['mimeType'],
                "size": file.get('size', 'Unknown'),
                "modifiedTime": file['modifiedTime']
            }
            file_list.append(file_info)
        
        return {
            "status": "success",
            "message": f"Found {len(files)} files matching query: {query}",
            "files": file_list
        }
        
    except Exception as e:
        return {
            "status": "error",
            "error_message": f"Failed to search files: {str(e)}"
        }

def get_file_metadata(file_id: str, tool_context: ToolContext) -> Dict[str, Any]:
    """Get metadata for a specific file."""
    try:
        service = get_google_drive_service()
        
        file = service.files().get(
            fileId=file_id,
            fields="id, name, mimeType, size, createdTime, modifiedTime, owners, permissions"
        ).execute()
        
        return {
            "status": "success",
            "file_id": file_id,
            "metadata": {
                "id": file['id'],
                "name": file['name'],
                "mimeType": file['mimeType'],
                "size": file.get('size', 'Unknown'),
                "createdTime": file['createdTime'],
                "modifiedTime": file['modifiedTime'],
                "owners": [owner['displayName'] for owner in file.get('owners', [])],
                "permissions": len(file.get('permissions', []))
            }
        }
        
    except Exception as e:
        return {
            "status": "error",
            "file_id": file_id,
            "error_message": f"Failed to get file metadata: {str(e)}"
        }

def get_file_sharing_permissions(file_id: str, tool_context: ToolContext) -> Dict[str, Any]:
    """Get detailed sharing permissions for a file, including email addresses of users it's shared with."""
    try:
        service = get_google_drive_service()
        
        # Get file basic info
        file = service.files().get(
            fileId=file_id,
            fields="id, name, mimeType, owners"
        ).execute()
        
        # Get all permissions for the file
        permissions_list = service.permissions().list(
            fileId=file_id,
            fields="permissions(id, type, role, emailAddress, displayName, domain, expirationTime)"
        ).execute()
        
        permissions = permissions_list.get('permissions', [])
        
        # Organize permissions by type
        shared_with = {
            'users': [],
            'groups': [],
            'domains': [],
            'anyone': []
        }
        
        for perm in permissions:
            perm_info = {
                'id': perm['id'],
                'role': perm['role'],  # owner, writer, commenter, reader
                'type': perm['type']   # user, group, domain, anyone
            }
            
            if 'emailAddress' in perm:
                perm_info['email'] = perm['emailAddress']
            if 'displayName' in perm:
                perm_info['displayName'] = perm['displayName']
            if 'domain' in perm:
                perm_info['domain'] = perm['domain']
            if 'expirationTime' in perm:
                perm_info['expirationTime'] = perm['expirationTime']
            
            # Categorize by type
            if perm['type'] == 'user':
                shared_with['users'].append(perm_info)
            elif perm['type'] == 'group':
                shared_with['groups'].append(perm_info)
            elif perm['type'] == 'domain':
                shared_with['domains'].append(perm_info)
            elif perm['type'] == 'anyone':
                shared_with['anyone'].append(perm_info)
        
        return {
            "status": "success",
            "file_id": file_id,
            "file_name": file['name'],
            "mime_type": file['mimeType'],
            "owners": [owner.get('emailAddress', owner.get('displayName', 'Unknown')) for owner in file.get('owners', [])],
            "total_permissions": len(permissions),
            "shared_with": shared_with,
            "message": f"Found {len(permissions)} sharing permissions for '{file['name']}'"
        }
        
    except Exception as e:
        return {
            "status": "error",
            "file_id": file_id,
            "error_message": f"Failed to get file sharing permissions: {str(e)}"
        }

def find_by_name(name: str, folder_id: str, tool_context: ToolContext) -> Dict[str, Any]:
    """Find files by name. Searches for files containing the name in the filename.
    If multiple matches found, returns them for user selection. If none found, shows available files."""
    try:
        service = get_google_drive_service()

        if not folder_id:
            folder_id = os.getenv('GOOGLE_DRIVE_FOLDER_ID')

        if not folder_id:
            return {
                "status": "error",
                "error_message": "No folder ID provided and GOOGLE_DRIVE_FOLDER_ID environment variable is not set"
            }

        # Search for files containing the name in the filename
        # Split the name into parts for more flexible matching
        name_parts = name.lower().split()
        
        # Build search query for files containing any part of the name
        name_queries = []
        for part in name_parts:
            if len(part) > 2:  # Only search for parts longer than 2 characters
                name_queries.append(f"name contains '{part}'")
        
        if not name_queries:
            name_queries = [f"name contains '{name.lower()}'"]
        
        # Combine queries with OR logic
        name_query = " or ".join(name_queries)
        
        # Full query for files in the folder
        search_query = f"'{folder_id}' in parents and ({name_query}) and (mimeType contains 'application/pdf' or mimeType contains 'application/vnd.google-apps.document' or mimeType contains 'text/plain')"
        
        results = service.files().list(
            q=search_query,
            pageSize=100,
            fields="nextPageToken, files(id, name, mimeType, size, modifiedTime)"
        ).execute()
        
        files = results.get('files', [])
        
        if not files:
            # If no files found by name, get all available files to show user
            all_files_query = f"'{folder_id}' in parents and (mimeType contains 'application/pdf' or mimeType contains 'application/vnd.google-apps.document' or mimeType contains 'text/plain')"
            
            all_results = service.files().list(
                q=all_files_query,
                pageSize=100,
                fields="nextPageToken, files(id, name, mimeType, size, modifiedTime)"
            ).execute()
            
            all_files = all_results.get('files', [])
            
            return {
                "status": "no_match_found",
                "message": f"No file found for '{name}'. Here are all available files:",
                "name": name,
                "available_files": [
                    {
                        "id": file['id'],
                        "name": file['name'],
                        "mimeType": file['mimeType'],
                        "size": file.get('size', 'Unknown'),
                        "modifiedTime": file['modifiedTime']
                    }
                    for file in all_files
                ],
                "total_available": len(all_files)
            }
        
        if len(files) == 1:
            # Single match found - return the file content
            file_id = files[0]['id']
            read_result = read_google_doc(file_id, tool_context)
            
            if read_result["status"] == "success":
                return {
                    "status": "single_match_found",
                    "message": f"Found file for '{name}': {files[0]['name']}",
                    "name": name,
                    "file": {
                        "id": files[0]['id'],
                        "name": files[0]['name'],
                        "mimeType": files[0]['mimeType'],
                        "size": files[0].get('size', 'Unknown'),
                        "modifiedTime": files[0]['modifiedTime']
                    },
                    "content": read_result["content"],
                    "content_preview": read_result["content"][:500] + "..." if len(read_result["content"]) > 500 else read_result["content"]
                }
            else:
                return read_result
        
        else:
            # Multiple matches found - return list for user selection
            return {
                "status": "multiple_matches_found",
                "message": f"Found {len(files)} files that might match '{name}'. Please specify which one you want:",
                "name": name,
                "matching_files": [
                    {
                        "id": file['id'],
                        "name": file['name'],
                        "mimeType": file['mimeType'],
                        "size": file.get('size', 'Unknown'),
                        "modifiedTime": file['modifiedTime']
                    }
                    for file in files
                ],
                "total_matches": len(files)
            }
        
    except Exception as e:
        return {
            "status": "error",
            "error_message": f"Failed to find file by name: {str(e)}"
        }
