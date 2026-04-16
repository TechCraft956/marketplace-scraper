#!/usr/bin/env python3
"""
DealScope Backend API Testing Suite
Tests all backend endpoints for the deal intelligence dashboard.
"""
import requests
import json
import sys
import io
from datetime import datetime

class DealScopeAPITester:
    def __init__(self, base_url="https://c61b15b4-d962-424d-9446-5c18be3292ca.preview.emergentagent.com"):
        self.base_url = base_url
        self.tests_run = 0
        self.tests_passed = 0
        self.test_results = []

    def log_test(self, name, success, details=""):
        """Log test result"""
        self.tests_run += 1
        if success:
            self.tests_passed += 1
            print(f"✅ {name}")
        else:
            print(f"❌ {name} - {details}")
        
        self.test_results.append({
            "test": name,
            "success": success,
            "details": details
        })

    def run_test(self, name, method, endpoint, expected_status=200, data=None, files=None):
        """Run a single API test"""
        url = f"{self.base_url}{endpoint}"
        headers = {'Content-Type': 'application/json'} if not files else {}
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=10)
            elif method == 'POST':
                if files:
                    response = requests.post(url, files=files, timeout=10)
                else:
                    response = requests.post(url, json=data, headers=headers, timeout=10)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers, timeout=10)
            else:
                self.log_test(name, False, f"Unsupported method: {method}")
                return False, {}

            success = response.status_code == expected_status
            response_data = {}
            
            try:
                response_data = response.json()
            except:
                response_data = {"raw_response": response.text}

            if success:
                self.log_test(name, True, f"Status: {response.status_code}")
            else:
                self.log_test(name, False, f"Expected {expected_status}, got {response.status_code}. Response: {response.text[:200]}")

            return success, response_data

        except Exception as e:
            self.log_test(name, False, f"Exception: {str(e)}")
            return False, {}

    def test_health_endpoint(self):
        """Test /api/health endpoint"""
        print("\n🔍 Testing Health Endpoint...")
        success, response = self.run_test(
            "Health endpoint returns 200 OK",
            "GET",
            "/api/health",
            200
        )
        
        if success and response.get("status") == "ok":
            self.log_test("Health endpoint returns correct status", True)
        elif success:
            self.log_test("Health endpoint returns correct status", False, f"Expected status 'ok', got {response.get('status')}")

    def test_listings_endpoint(self):
        """Test /api/listings endpoint with various parameters"""
        print("\n🔍 Testing Listings Endpoint...")
        
        # Basic listings fetch
        success, response = self.run_test(
            "Get listings returns 200",
            "GET",
            "/api/listings",
            200
        )
        
        if success:
            listings = response.get("listings", [])
            total = response.get("total", 0)
            
            if len(listings) > 0:
                self.log_test("Listings endpoint returns data", True, f"Found {len(listings)} listings")
                
                # Check if listings are sorted by score descending by default
                if len(listings) > 1:
                    scores = [listing.get("score", 0) for listing in listings]
                    is_sorted_desc = all(scores[i] >= scores[i+1] for i in range(len(scores)-1))
                    self.log_test("Listings sorted by score descending by default", is_sorted_desc, 
                                f"Scores: {scores[:5]}...")
                
                # Test a specific listing ID for individual fetch
                if listings:
                    listing_id = listings[0].get("id")
                    if listing_id:
                        self.run_test(
                            "Get individual listing by ID",
                            "GET",
                            f"/api/listings/{listing_id}",
                            200
                        )
            else:
                self.log_test("Listings endpoint returns data", False, "No listings found")

        # Test filtering by category
        self.run_test(
            "Filter listings by category (electronics)",
            "GET",
            "/api/listings?category=electronics",
            200
        )

        # Test filtering by min_score
        self.run_test(
            "Filter listings by min_score=30",
            "GET",
            "/api/listings?min_score=30",
            200
        )

        # Test filtering by max_price
        self.run_test(
            "Filter listings by max_price=1000",
            "GET",
            "/api/listings?max_price=1000",
            200
        )

        # Test search functionality
        self.run_test(
            "Search listings for 'macbook'",
            "GET",
            "/api/listings?search=macbook",
            200
        )

        # Test sorting by price ascending
        success, response = self.run_test(
            "Sort listings by price ascending",
            "GET",
            "/api/listings?sort_by=price&sort_order=asc",
            200
        )
        
        if success:
            listings = response.get("listings", [])
            if len(listings) > 1:
                prices = [listing.get("price", 0) for listing in listings if listing.get("price") is not None]
                if prices:
                    is_sorted_asc = all(prices[i] <= prices[i+1] for i in range(len(prices)-1))
                    self.log_test("Price sorting ascending works", is_sorted_asc, 
                                f"Prices: {prices[:5]}...")

    def test_stats_endpoint(self):
        """Test /api/stats endpoint"""
        print("\n🔍 Testing Stats Endpoint...")
        success, response = self.run_test(
            "Get stats returns 200",
            "GET",
            "/api/stats",
            200
        )
        
        if success:
            required_fields = ["total_listings", "active_listings", "hot_deals", "category_counts", "score_distribution"]
            missing_fields = [field for field in required_fields if field not in response]
            
            if not missing_fields:
                self.log_test("Stats endpoint returns all required fields", True)
                
                # Check category_counts structure
                cat_counts = response.get("category_counts", {})
                if isinstance(cat_counts, dict) and len(cat_counts) > 0:
                    self.log_test("Category counts present", True, f"Categories: {list(cat_counts.keys())}")
                else:
                    self.log_test("Category counts present", False, "No category counts found")
                
                # Check score_distribution structure
                score_dist = response.get("score_distribution", {})
                expected_score_keys = ["hot", "good", "fair", "low"]
                if all(key in score_dist for key in expected_score_keys):
                    self.log_test("Score distribution has correct structure", True)
                else:
                    self.log_test("Score distribution has correct structure", False, 
                                f"Missing keys: {[k for k in expected_score_keys if k not in score_dist]}")
            else:
                self.log_test("Stats endpoint returns all required fields", False, f"Missing: {missing_fields}")

    def test_categories_endpoint(self):
        """Test /api/categories endpoint"""
        print("\n🔍 Testing Categories Endpoint...")
        success, response = self.run_test(
            "Get categories returns 200",
            "GET",
            "/api/categories",
            200
        )
        
        if success and isinstance(response, list):
            if len(response) > 0:
                self.log_test("Categories endpoint returns list with data", True, f"Found {len(response)} categories")
                
                # Check structure of first category
                first_cat = response[0]
                required_fields = ["name", "count", "avg_score"]
                if all(field in first_cat for field in required_fields):
                    self.log_test("Category objects have correct structure", True)
                else:
                    missing = [f for f in required_fields if f not in first_cat]
                    self.log_test("Category objects have correct structure", False, f"Missing: {missing}")
            else:
                self.log_test("Categories endpoint returns list with data", False, "Empty list returned")

    def test_listing_actions(self):
        """Test mark-sold, mark-contacted, and delete actions"""
        print("\n🔍 Testing Listing Actions...")
        
        # First get a listing to test with
        success, response = self.run_test(
            "Get listings for action testing",
            "GET",
            "/api/listings?limit=1",
            200
        )
        
        if success and response.get("listings"):
            listing_id = response["listings"][0]["id"]
            
            # Test mark-contacted
            self.run_test(
                "Mark listing as contacted",
                "POST",
                f"/api/listings/{listing_id}/mark-contacted",
                200
            )
            
            # Test mark-sold
            self.run_test(
                "Mark listing as sold",
                "POST",
                f"/api/listings/{listing_id}/mark-sold",
                200
            )
            
            # Test delete (this will actually delete the listing)
            # Let's get another listing for delete test
            success2, response2 = self.run_test(
                "Get another listing for delete test",
                "GET",
                "/api/listings?limit=2",
                200
            )
            
            if success2 and len(response2.get("listings", [])) > 1:
                delete_listing_id = response2["listings"][1]["id"]
                self.run_test(
                    "Delete listing",
                    "DELETE",
                    f"/api/listings/{delete_listing_id}",
                    200
                )
        else:
            self.log_test("Get listings for action testing", False, "No listings available for testing actions")

    def test_import_endpoints(self):
        """Test JSON and CSV import endpoints"""
        print("\n🔍 Testing Import Endpoints...")
        
        # Test JSON import
        sample_json_data = [
            {
                "title": "Test MacBook Pro",
                "price": 1200,
                "location": "Austin, TX",
                "description": "Test listing for import",
                "category": "electronics"
            }
        ]
        
        # Create a temporary JSON file in memory
        json_content = json.dumps(sample_json_data)
        json_file = io.StringIO(json_content)
        
        try:
            files = {'file': ('test_import.json', json_content, 'application/json')}
            success, response = self.run_test(
                "Import JSON file",
                "POST",
                "/api/import/json",
                200,
                files=files
            )
            
            if success:
                imported = response.get("imported", 0)
                self.log_test("JSON import processes data", imported > 0, f"Imported: {imported}")
        except Exception as e:
            self.log_test("Import JSON file", False, f"Exception during JSON import: {str(e)}")

        # Test CSV import
        csv_content = "title,price,location,description,category\nTest Gaming PC,800,Austin TX,Test PC for import,electronics"
        
        try:
            files = {'file': ('test_import.csv', csv_content, 'text/csv')}
            success, response = self.run_test(
                "Import CSV file",
                "POST",
                "/api/import/csv",
                200,
                files=files
            )
            
            if success:
                imported = response.get("imported", 0)
                self.log_test("CSV import processes data", imported > 0, f"Imported: {imported}")
        except Exception as e:
            self.log_test("Import CSV file", False, f"Exception during CSV import: {str(e)}")

    def run_all_tests(self):
        """Run all backend tests"""
        print("🚀 Starting DealScope Backend API Tests")
        print(f"Testing against: {self.base_url}")
        print("=" * 60)
        
        self.test_health_endpoint()
        self.test_listings_endpoint()
        self.test_stats_endpoint()
        self.test_categories_endpoint()
        self.test_listing_actions()
        self.test_import_endpoints()
        
        print("\n" + "=" * 60)
        print(f"📊 Test Results: {self.tests_passed}/{self.tests_run} passed")
        
        if self.tests_passed == self.tests_run:
            print("🎉 All tests passed!")
            return 0
        else:
            print("❌ Some tests failed. Check the details above.")
            failed_tests = [r for r in self.test_results if not r["success"]]
            print("\nFailed tests:")
            for test in failed_tests:
                print(f"  - {test['test']}: {test['details']}")
            return 1

def main():
    tester = DealScopeAPITester()
    return tester.run_all_tests()

if __name__ == "__main__":
    sys.exit(main())