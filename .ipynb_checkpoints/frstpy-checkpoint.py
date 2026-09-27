import requests

response = requests.get("https://www.facebook.com")

print("Status Code:", response.status_code)
print("First 200 characters:")
print(response.text[:200])