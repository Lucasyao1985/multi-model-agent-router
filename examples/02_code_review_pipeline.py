"""
Example 2: Full code review pipeline
Review → Auto-fix → Test validation, each step routed optimally.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import OpenRouterClient
from src.agents.code_review_agent import CodeReviewAgent

# Deliberately buggy code for demonstration
BUGGY_CODE = '''
import hashlib

def authenticate_user(username, password, db):
    # WARNING: multiple issues here intentionally
    query = f"SELECT * FROM users WHERE username = '{username}'"
    user = db.execute(query).fetchone()
    
    if user:
        stored_hash = user["password"]
        if stored_hash == password:   # plaintext comparison
            return True
    return False

def get_user_data(user_id):
    data = {}
    for i in range(1000000):        # unnecessary loop
        data[i] = i * 2
    return data.get(user_id)

def process_items(items):
    result = []
    for item in items:
        try:
            result.append(int(item))
        except:                     # bare except
            pass
    return result
'''

client = OpenRouterClient()
agent = CodeReviewAgent(client)

print("Running 3-step code review pipeline...\n")
result = agent.run_pipeline(BUGGY_CODE, language="python")

print("\n--- REVIEW SUMMARY ---")
print(f"Risk Level: {result.review.risk_level.upper()}")
for issue in result.review.issues:
    print(f"  [{issue.get('severity', '?').upper()}] {issue.get('location', '')} — {issue.get('description', '')}")

print("\n--- CHANGES MADE ---")
for change in result.fix.changes_made:
    print(f"  ✓ {change}")

print("\n--- VALIDATION ---")
print(f"Overall: {'✓ PASSED' if result.validation.passed else '✗ FAILED'} "
      f"(coverage: {result.validation.coverage_estimate})")
for tc in result.validation.test_cases:
    icon = "✓" if tc.get("passed") else "✗"
    print(f"  {icon} {tc.get('name', '')}")

print(f"\n--- PIPELINE COST ---")
print(f"Models used: {' → '.join(result.models_used)}")
print(f"Total tokens: {result.total_tokens:,}")
print(f"Total cost: ${result.total_cost_usd:.5f}")
