"""Connectivity test: verify OpenHands Agent Server REST endpoints."""

import httpx

BASE = "http://localhost:3005"


def test_openapi():
    """Test /openapi.json endpoint."""
    print("[1/3] Testing /openapi.json ...")
    client = httpx.Client(base_url=BASE, timeout=10)
    try:
        resp = client.get("/openapi.json")
        print(f"   HTTP {resp.status_code}  body={repr(resp.text)[:200]}")
        client.close()
        return resp.status_code == 200
    except Exception as e:
        print(f"   Error: {e}")
        client.close()
        return False


def test_conversations():
    """Test POST /api/conversations."""
    print("\n[2/3] Testing POST /api/conversations ...")
    client = httpx.Client(base_url=BASE, timeout=30)
    payload = {"goal": "Reply with 'connection test passed'"}
    try:
        resp = client.post("/api/conversations", json=payload)
        print(f"   HTTP {resp.status_code}  body={repr(resp.text)[:300]}")
        client.close()
        return resp.status_code == 200
    except Exception as e:
        print(f"   Error: {e}")
        client.close()
        return False


def test_conversation_id():
    """Test GET /api/conversations/{id}."""
    print("\n[3/3] Testing GET /api/conversations (list) ...")
    client = httpx.Client(base_url=BASE, timeout=10)
    try:
        resp = client.get("/api/conversations")
        print(f"   HTTP {resp.status_code}  body={repr(resp.text)[:300]}")
        client.close()
        return resp.status_code in (200, 201, 401, 403)
    except Exception as e:
        print(f"   Error: {e}")
        client.close()
        return False


def main():
    print("=" * 60)
    print("OpenHands Agent Server - Connectivity Test")
    print("=" * 60)
    r1 = test_openapi()
    r2 = test_conversations()
    r3 = test_conversation_id()
    print("\n" + "=" * 60)
    print(f"Results: openapi={r1}  create={r2}  list={r3}")
    if all([r1, r2, r3]):
        print("All tests PASSED.")
    else:
        print("Some tests FAILED - check OpenHands logs: docker logs openhands-server")
    print("=" * 60)


if __name__ == "__main__":
    main()