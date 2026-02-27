#!/usr/bin/env python3
"""
Setup script for Google Service Account authentication.
This replaces the OAuth2 flow with Service Account authentication.
"""

import os
import json
from pathlib import Path

def setup_service_account():
    """Guide through Service Account setup."""
    print("🔧 Google Service Account Setup")
    print("=" * 40)
    
    print("\n1. Create a Service Account:")
    print("   - Go to https://console.cloud.google.com/")
    print("   - Navigate to IAM & Admin > Service Accounts")
    print("   - Click 'Create Service Account'")
    print("   - Give it a name (e.g., 'mate-agent-drive')")
    print("   - Grant 'Drive File Stream' role")
    
    print("\n2. Create and download the key:")
    print("   - Click on your service account")
    print("   - Go to 'Keys' tab")
    print("   - Click 'Add Key' > 'Create new key'")
    print("   - Choose JSON format")
    print("   - Download the key file")
    
    print("\n3. Configure authentication:")
    print("   Choose one of these methods:")
    
    # Method 1: Environment variable
    print("\n   Method 1 - Environment Variable (Recommended):")
    print("   - Set GOOGLE_SERVICE_ACCOUNT_INFO environment variable")
    print("   - Value should be the entire JSON content of your key file")
    print("   - Example: export GOOGLE_SERVICE_ACCOUNT_INFO='{\"type\":\"service_account\",...}'")
    
    # Method 2: Key file
    print("\n   Method 2 - Key File:")
    print("   - Place your downloaded key file in the project root")
    print("   - Rename it to 'service-account-key.json'")
    print("   - Or set GOOGLE_SERVICE_ACCOUNT_FILE environment variable to point to your key file")
    
    # Method 3: GCP default credentials
    print("\n   Method 3 - GCP Default Credentials:")
    print("   - If running on GCP (Cloud Run, Compute Engine, etc.)")
    print("   - Use: gcloud auth application-default login")
    print("   - Or assign service account to the instance")
    
    print("\n4. Share the folder:")
    print("   - In Google Drive, right-click your CV folder")
    print("   - Click 'Share'")
    print("   - Add your service account email (ends with @project-id.iam.gserviceaccount.com)")
    print("   - Give it 'Viewer' permissions")
    
    print("\n5. Set folder ID:")
    print("   - Set GOOGLE_DRIVE_FOLDER_ID environment variable")
    print("   - Get folder ID from the URL when you open the folder in Drive")
    print("   - Example: export GOOGLE_DRIVE_FOLDER_ID='1ABC123...'")
    
    print("\n✅ Setup complete! Your app will now use Service Account authentication.")

def create_env_example():
    """Create .env.example file with required variables."""
    env_example = """# Google Service Account Configuration
# Choose one of these methods:

# Method 1: JSON content as environment variable (recommended)
GOOGLE_SERVICE_ACCOUNT_INFO={"type":"service_account","project_id":"your-project","private_key_id":"...","private_key":"-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n","client_email":"your-service-account@your-project.iam.gserviceaccount.com","client_id":"...","auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs","client_x509_cert_url":"https://www.googleapis.com/robot/v1/metadata/x509/your-service-account%40your-project.iam.gserviceaccount.com"}

# Method 2: Path to key file (alternative)
# GOOGLE_SERVICE_ACCOUNT_FILE=path/to/your/service-account-key.json

# Google Drive folder containing CV files
GOOGLE_DRIVE_FOLDER_ID=your_folder_id_here
"""
    
    with open('.env.example', 'w') as f:
        f.write(env_example)
    
    print("\n📝 Created .env.example file with configuration template")

if __name__ == "__main__":
    setup_service_account()
    create_env_example()
    
    print("\n🚀 Next steps:")
    print("1. Follow the setup instructions above")
    print("2. Copy .env.example to .env and fill in your values")
    print("3. Test with: python test_cv_agent.py")
