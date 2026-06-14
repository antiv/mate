#!/usr/bin/env python3
import os
import sys
import httpx
import hashlib
from datetime import datetime, timezone

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.database_client import get_database_client
from shared.utils.models import PersonalAccessToken, AgentConfig, User

def test_local_api():
    print("🚀 Starting local API verification...")
    db = get_database_client()
    session = db.get_session()
    
    if not session:
        print("❌ Database session could not be created")
        sys.exit(1)
        
    try:
        # 1. Ensure a user exists to associate with the PAT
        test_username = "admin"
        user = session.query(User).filter_by(user_id=test_username).first()
        if not user:
            print("👤 Creating test admin user...")
            user = User(user_id=test_username, roles='["admin"]')
            session.add(user)
            session.commit()
            
        # 2. Expose a test root agent (e.g., 'chess_mate_root')
        agent = session.query(AgentConfig).filter(
            (AgentConfig.parent_agents.is_(None) | 
             (AgentConfig.parent_agents == "") | 
             (AgentConfig.parent_agents == "[]"))
        ).first()
        
        if not agent:
            print("🤖 Creating a dummy root agent config...")
            agent = AgentConfig(
                name="test_root_agent",
                type="llm",
                disabled=False,
                expose_as_model=True,
                project_id=1,
                parent_agents="[]"
            )
            session.add(agent)
            session.commit()
            print(f"🤖 Created dummy root agent: {agent.name}")
        else:
            print(f"🤖 Setting agent '{agent.name}' expose_as_model = True")
            agent.expose_as_model = True
            session.commit()
            
        # 3. Create a temporary PAT
        test_token = "mate_pat_verification_token_12345"
        token_hash = hashlib.sha256(test_token.encode("utf-8")).hexdigest()
        
        # Clean up existing verification token if any
        existing_pat = session.query(PersonalAccessToken).filter_by(token_hash=token_hash).first()
        if existing_pat:
            session.delete(existing_pat)
            session.commit()
            
        print("🔑 Creating temporary PAT in database...")
        pat = PersonalAccessToken(
            token_hash=token_hash,
            token_prefix=test_token[:13],
            name="Verification Test Token",
            user_id=test_username
        )
        session.add(pat)
        session.commit()
        
        # 4. Request /v1/models from the running auth server
        print("📡 Sending request to http://localhost:8000/v1/models...")
        headers = {"Authorization": f"Bearer {test_token}"}
        
        with httpx.Client() as client:
            response = client.get("http://localhost:8000/v1/models", headers=headers)
            print(f"📥 Response status: {response.status_code}")
            print(f"📥 Response body: {response.text}")
            
            # Assert status code is 200
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            
            # Assert response body contains exposed models
            data = response.json()
            assert "data" in data, "Response JSON missing 'data' field"
            
            models = [m["id"] for m in data["data"]]
            print(f"✅ Found models: {models}")
            assert agent.name in models, f"Expected model '{agent.name}' to be exposed, got {models}"
            
        print("🎉 API verification PASSED successfully!")
        
        # Clean up PAT
        session.delete(pat)
        session.commit()
        print("🧹 Cleaned up temporary PAT")
        
    except Exception as e:
        print(f"❌ Verification failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        session.close()

if __name__ == "__main__":
    test_local_api()
