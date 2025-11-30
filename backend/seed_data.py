#!/usr/bin/env python3
"""
Seed script to populate the personal memory database with test data.
Run this after the services are up: python seed_data.py
"""

import requests
import time
from datetime import datetime, timedelta

API_BASE = "http://localhost:8000"

def wait_for_api():
    """Wait for the API to be ready"""
    print("Waiting for API to be ready...")
    for i in range(30):
        try:
            resp = requests.get(f"{API_BASE}/docs", timeout=2)
            if resp.status_code == 200:
                print("✓ API is ready!")
                return True
        except:
            pass
        time.sleep(2)
    print("✗ API not ready after 60 seconds")
    return False

def ingest_contact(
    contact_id,
    display_name,
    aliases=None,
    birthday=None,
    emails=None,
    phones=None,
    links=None,
    tags=None,
    relationship=None,
):
    """Add a contact to the database"""
    data = {
        "contact_id": contact_id,
        "display_name": display_name,
        "aliases": aliases or [],
        "birthday": birthday,
        "emails": emails or [],
        "phones": phones or [],
        "links": links or [],
        "tags": tags or [],
        "relationship": relationship,
    }
    resp = requests.post(f"{API_BASE}/ingest/contact", json=data)
    print(f"  Added contact: {display_name} ({contact_id})")
    return resp.json()

def ingest_place(place_id, name, city=None, country=None, lat=None, lon=None):
    """Add a place to the database"""
    data = {
        "place_id": place_id,
        "name": name,
        "city": city,
        "country": country,
        "lat": lat,
        "lon": lon
    }
    resp = requests.post(f"{API_BASE}/ingest/place", json=data)
    print(f"  Added place: {name} ({place_id})")
    return resp.json()

def ingest_event(event_id, start_date, summary, title=None, place_id=None, people=None, tags=None, types=None):
    """Add an event to the database"""
    computed_title = (title or "").strip()
    if not computed_title:
        first_line = (summary or "").strip().split(".")[0]
        computed_title = first_line.strip() or event_id
    data = {
        "id": event_id,
        "start_date": start_date,
        "title": computed_title,
        "summary": summary,
        "place_id": place_id,
        "people": people or [],
        "tags": tags or [],
        "types": types or ["generic"],
        "raw": {"source": "seed_script"}
    }
    resp = requests.post(f"{API_BASE}/ingest/event", json=data)
    print(f"  Added event: {event_id}")
    return resp.json()

def seed_all():
    """Populate the database with sample data"""
    print("\n🌱 Starting to seed database...\n")
    
    # Add contacts
    print("📇 Adding contacts...")
    ingest_contact(
        "contact:alice#001",
        "Alice Chen",
        ["Alice", "Alice C.", "A. Chen"],
        birthday="1990-04-15",
        emails=["alice@example.com", "alice.work@example.com"],
        phones=["+1-415-555-0101"],
        links=["https://www.linkedin.com/in/alicechen"],
        tags=["product", "runner"],
        relationship="Coworker",
    )
    ingest_contact(
        "contact:bob#002",
        "Bob Martinez",
        ["Bob", "Roberto", "Bob M."],
        birthday="1988-09-30",
        emails=["bob@example.com"],
        phones=["+1-415-555-0202"],
        links=["https://github.com/bmartinez"],
        tags=["engineering", "travel"],
        relationship="Friend",
    )
    ingest_contact(
        "contact:carol#003",
        "Carol Singh",
        ["Carol", "C. Singh"],
        birthday="1992-01-08",
        emails=["carol@example.com"],
        phones=["+1-415-555-0303"],
        links=["https://carolsingh.com"],
        tags=["analytics", "consulting"],
        relationship="Coworker",
    )
    ingest_contact(
        "contact:dave#004",
        "Dave Johnson",
        ["Dave", "David", "DJ"],
        birthday="1985-06-22",
        emails=["dave@example.com"],
        phones=["+1-415-555-0404"],
        links=["https://www.strava.com/athletes/davej"],
        tags=["engineering", "fitness"],
        relationship="Brother",
    )
    
    # Add places
    print("\n📍 Adding places...")
    ingest_place("plc_cafe_downtown", "Downtown Café", "San Francisco", "US", 37.7749, -122.4194)
    ingest_place("plc_office_main", "Main Office", "San Francisco", "US", 37.7849, -122.4094)
    ingest_place("plc_park_golden_gate", "Golden Gate Park", "San Francisco", "US", 37.7694, -122.4862)
    ingest_place("plc_restaurant_sushi", "Sushi Place", "San Francisco", "US", 37.7949, -122.3994)
    ingest_place("plc_gym_fitness", "Fitness First Gym", "San Francisco", "US", 37.7649, -122.4294)
    
    # Add events
    print("\n📅 Adding events...")
    now = datetime.now()
    
    # Recent events
    ingest_event(
        f"evt_{(now - timedelta(days=2)).strftime('%Y%m%d')}_001",
        (now - timedelta(days=2)).isoformat(),
        "Had breakfast with Alice and Bob at the downtown café. Discussed the new product roadmap and decided to focus on mobile features next quarter.",
        place_id="plc_cafe_downtown",
        people=["contact:alice#001", "contact:bob#002"],
        tags=["work", "planning", "breakfast"],
        types=["meeting", "communication"]
    )
    
    ingest_event(
        f"evt_{(now - timedelta(days=5)).strftime('%Y%m%d')}_001",
        (now - timedelta(days=5)).isoformat(),
        "Team meeting at the main office. Carol presented the Q4 metrics. Revenue is up 23% year over year. Celebrated with cake.",
        place_id="plc_office_main",
        people=["contact:alice#001", "contact:bob#002", "contact:carol#003", "contact:dave#004"],
        tags=["work", "meeting", "metrics"],
        types=["meeting", "celebration"]
    )
    
    ingest_event(
        f"evt_{(now - timedelta(days=7)).strftime('%Y%m%d')}_001",
        (now - timedelta(days=7)).isoformat(),
        "Morning jog with Dave at Golden Gate Park. Beautiful weather. Talked about his new side project building a mobile app for runners.",
        place_id="plc_park_golden_gate",
        people=["contact:dave#004"],
        tags=["exercise", "social", "outdoors"],
        types=["health", "personal"]
    )
    
    ingest_event(
        f"evt_{(now - timedelta(days=10)).strftime('%Y%m%d')}_001",
        (now - timedelta(days=10)).isoformat(),
        "Lunch with Bob at the sushi place. He recommended the omakase - it was incredible! We also discussed his upcoming trip to Japan.",
        place_id="plc_restaurant_sushi",
        people=["contact:bob#002"],
        tags=["food", "lunch", "social"],
        types=["interaction", "communication"]
    )
    
    ingest_event(
        f"evt_{(now - timedelta(days=14)).strftime('%Y%m%d')}_001",
        (now - timedelta(days=14)).isoformat(),
        "Coffee chat with Alice about the engineering challenges in the authentication system. She suggested using OAuth2 with PKCE flow.",
        place_id="plc_cafe_downtown",
        people=["contact:alice#001"],
        tags=["work", "technical", "coffee"],
        types=["communication", "meeting"]
    )
    
    ingest_event(
        f"evt_{(now - timedelta(days=20)).strftime('%Y%m%d')}_001",
        (now - timedelta(days=20)).isoformat(),
        "Gym session in the morning. Did a full body workout focusing on compound exercises. Feeling stronger!",
        place_id="plc_gym_fitness",
        people=[],
        tags=["exercise", "fitness", "personal"],
        types=["health", "personal"]
    )
    
    ingest_event(
        f"evt_{(now - timedelta(days=25)).strftime('%Y%m%d')}_001",
        (now - timedelta(days=25)).isoformat(),
        "Dinner with Carol at the sushi place. She's thinking about starting a consulting business. We brainstormed potential niches and pricing strategies.",
        place_id="plc_restaurant_sushi",
        people=["contact:carol#003"],
        tags=["social", "dinner", "business"],
        types=["communication", "interaction"]
    )
    
    ingest_event(
        f"evt_{(now - timedelta(days=30)).strftime('%Y%m%d')}_001",
        (now - timedelta(days=30)).isoformat(),
        "All-hands meeting at the office. CEO announced the new vision for 2026. Focus on AI integration and international expansion to Europe and Asia.",
        place_id="plc_office_main",
        people=["contact:alice#001", "contact:bob#002", "contact:carol#003", "contact:dave#004"],
        tags=["work", "meeting", "strategy"],
        types=["meeting", "communication"]
    )
    
    print("\n✅ Database seeded successfully!")
    print("\n💡 Try these example queries:")
    print('  curl -X POST http://localhost:8000/resolve -H "Content-Type: application/json" -d \'{"text":"When did I last meet Alice for coffee?"}\'')
    print('  curl -X POST http://localhost:8000/search -H "Content-Type: application/json" -d \'{"query":"discussions about work and planning","limit":3}\'')

if __name__ == "__main__":
    if wait_for_api():
        seed_all()
    else:
        print("Failed to connect to API. Make sure docker-compose services are running.")

