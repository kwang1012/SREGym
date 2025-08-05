import random
import time
from locust import HttpUser, task, between
import json

class TrainTicketUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        self.client.verify = False
        self.login()
        self.get_contacts()

    def login(self):
        response = self.client.post("/api/v1/users/login",
            json={
                "username": "fdse_microservice",
                "password": "111111"
            },
            headers={"Content-Type": "application/json"}
        )
        if response.status_code == 200:
            data = response.json()
            self.token = data.get("data", {}).get("token", "")
            self.user_id = data.get("data", {}).get("userId", "")
            self.headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }
        else:
            print(f"Login failed: {response.status_code}")
            self.token = ""
            self.user_id = ""
            self.headers = {"Content-Type": "application/json"}

    def get_contacts(self):
        if not self.user_id:
            return
        
        response = self.client.get(f"/api/v1/contactservice/contacts/account/{self.user_id}",
            headers=self.headers,
            name="/contacts/get"
        )
        
        if response.status_code == 200:
            data = response.json()
            contacts = data.get("data", [])
            if contacts:
                self.contact_id = contacts[0].get("id", "")
            else:
                self.contact_id = ""
        else:
            self.contact_id = ""

    @task(3)
    def search_tickets(self):
        self.client.get("/api/v1/travelservice/trips/left",
            headers=self.headers,
            name="/trips/search"
        )

    @task(2)
    def view_orders(self):
        if self.user_id:
            self.client.get(f"/api/v1/orderservice/order/refresh",
                headers=self.headers,
                name="/orders/view"
            )

    @task(1)
    def create_and_cancel_order(self):
        if not self.user_id or not self.contact_id:
            return

        # Create order
        order_data = {
            "accountId": self.user_id,
            "contactsId": self.contact_id,
            "tripId": "D1345",
            "seatType": 2,
            "date": "2025-07-01",
            "from": "Shanghai",
            "to": "Beijing"
        }

        create_response = self.client.post("/api/v1/preserveservice/preserve",
            json=order_data,
            headers=self.headers,
            name="/order/create"
        )

        if create_response.status_code == 200:
            try:
                response_data = create_response.json()
                if isinstance(response_data, dict):
                    order_info = response_data.get("data", response_data)
                    if isinstance(order_info, dict):
                        order_id = order_info.get("orderId") or order_info.get("id")
                    else:
                        order_id = None
                else:
                    order_id = None
                
                if order_id:
                    time.sleep(2)

                    self.client.get(f"/api/v1/cancelservice/cancel/{order_id}",
                        headers=self.headers,
                        name="/order/cancel"
                    )
            except Exception as e:
                print(f"Error processing order response: {e}")

    @task(5)
    def browse_stations(self):
        self.client.get("/api/v1/stationservice/stations",
            headers=self.headers,
            name="/stations/list"
        ) 