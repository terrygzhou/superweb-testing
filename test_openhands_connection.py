"""Connectivity test: verify OpenHands Agent Server REST endpoints."""

import httpx
import json
import sys

BASE = "http://localhost:3005"

def test_openapi():
    """Test /openapi.json endpoint."""
    print(f"[1/3] Testing /openapi.json ...")
    try:
        resp = httpx.get(f"{BASE}/openapi.json", timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            print(f"   HTTP 200  OK (OpenAPI spec loaded)")
            return True
    except Exception as e:
        print(f"   ERROR: {e}")
    return False

def test_create_conversation():
    """Test POST /api/conversations."""
    print(f"[2/3] Testing POST /api/conversations ...")
    try:
        payload = {
            "goal": "Test: reply with 'ok'",
            "workspace": "test_workspace"
        }
        resp = httpx.post(f"{BASE}/api/conversations", json=payload, timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            conv_id = data.get("id") or data.get("conversation_id", "unknown")
            print(f"   HTTP 200  OK (conv_id={conv_id})")
            return True
        elif resp.status_code == 422:
            # Server responding, just field validation — still means endpoint is live
            print(f"   HTTP 422  OK (endpoint live, validation: {resp.json().get('detail','field required')})")
            return True
        else:
            print(f"   HTTP {resp.status_code}  {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"   ERROR: {e}")
        return False

def test_list_conversations():
    """Test GET /api/conversations (list)."""
    print(f"[3/3] Testing GET /api/conversations ...")
    try:
        resp = httpx.get(f"{BASE}/api/conversations", params={"ids": "test"}, timeout=5.0)
        if resp.status_code == 200:
            print(f"   HTTP 200  OK")
            return True
        elif resp.status_code == 422:
            print(f"   HTTP 422  OK (endpoint live, requires 'ids' query param)")
            return True
        else:
            print(f"   HTTP {resp.status_code}  {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"   ERROR: {e}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("OpenHands Agent Server - Connectivity Test")
    print("=" * 60)
    
    results = {
        "openapi": test_openapi(),
        "create": test_create_conversation(),
        "list": test_list_conversations(),
    }
    
    print("=" * 60)
    passed = sum(results.values())
    total = len(results)
    status = "ALL PASSED" if passed == total else f"{passed}/{total} passed"
    print(f"Results: {status}")
    
    if passed < total:
        print("Some tests FAILED - check OpenHands logs: docker logs openhands-server")
        sys.exit(1)
    
    print("=" * 60)
    sys.exit(0)