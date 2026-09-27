import requests


def factorial(n):
	if n < 0:
		raise ValueError("factorial is not defined for negative numbers")

	result = 1
	for number in range(2, n + 1):
		result *= number
	return result


response = requests.get("https://www.facebook.com")

print("Status Code:", response.status_code)
print("First 200 characters:")
print(response.text[:200])
print("Factorial of 5:", factorial(5))