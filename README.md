# Distance Vector Router

This repository contains the implementation for a router which uses the distance bector routing algorithm.

# Running the router

1. Install the dependencies with `pip install -r requirements.txt`;
2. Create a file named `roteadores.txt` with the IP address of your router in the first line and the IP addresses of all of its neighbors in the subsequent lines, each in a separate line;
3. Run the router with `python router.py`. Optionally, you can supply the `-v` (or `--verbose`) command-line flag if you would like to see debug output.

**NOTICE**: The outputs are directed to a file named `output.txt`.