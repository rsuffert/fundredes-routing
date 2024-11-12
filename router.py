from typing import List, Dict, Tuple
import schedule
import time
import socket
import threading
import re
import logging
import argparse

# Configurations
OUTPUT_FILE:    str = "output.txt"
NEIGHBORS_FILE: str = "roteadores.txt"
PORT:           int = 19_000

# These are expressed IN SECONDS
SEND_ROUTES_INTERVAL:         int = 15
STALE_ROUTE_THRESHOLD:        int = 35
SHOW_ROUTING_TABLE_INTERVAL:  int = 20

# Regexes for parsing messages
ROUTING_TABLE_PATTERN:        str = r'!(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)'
ROUTING_ANNOUNCEMENT_PATTERN: str = r'@(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'
PLAIN_TEXT_PATTERN:           str = r'&(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})%(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})%(.+)'

class Path:
    """
    Represents a path for a routing table.
    """
    def __init__(self, out_address: str, metric: int) -> None:
        """
        Initializes the path.
        Args:
            out_address (str): The IP address of the next hop.
            metric (int): The metric associated with the path. The lower it is, the better the path is.
        """
        self.out_address: str = out_address
        self.metric:      int = metric
        self.timestamp: float = time.time()

class Router:
    def __init__(self) -> None:
        """
        Initializes the router.
        """
        logging.debug("Loading neighbors and my IP...")
        my_ip, neighbors = self._load_neighbors()
        self.neighbors: List[str] = neighbors
        self.my_ip: str = my_ip

        logging.debug("Building initial routing table...")
        self.table: Dict[str, Path] = {}
        self.table_mutex = threading.Lock()
        for neighbor in self.neighbors:
            self.table_mutex.acquire()
            self.table[neighbor] = Path(neighbor, 1)
            self.table_mutex.release()
        
        logging.debug("Creating UDP socket...")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.my_ip, PORT))
        self.sock_mutex = threading.Lock()

    def _load_neighbors(self) -> Tuple[str, List[str]]:
        """
        Loads the router's neighbors and its own IP address.
        Returns:
            Tuple[str, List[str]]: A tuple with the router's IP address and a list of its neighbors' IP addresses.
        """
        neighbors: List[str] = []
        with open(NEIGHBORS_FILE, 'r') as f:
            my_ip = f.readline().strip()
            for ip in f:
                neighbors.append(ip.strip())
        return my_ip, neighbors

    def _show_routing_table(self) -> None:
        """
        Prints the routing table of the router instance in a formatted way.
        """
        table: str = f"{'Destination IP':<20} {'Metric':<10} {'Output IP':<20}\n"
        table += f"{'='*50}\n"
        for dest_ip, path in self.table.items():
            table += f"{dest_ip:<20} {path.metric:<10} {path.out_address:<20}\n"
        table = table.strip('\n')
        logging.info(f"Routing table:\n{table}")

    def _send_routes(self) -> None:
        """
        Sends the routing table to all neighbors.
        """
        # parse the routing table into the required format
        message: str = ""
        for dest_ip, path in self.table.items():
            message += f"!{path.out_address}:{path.metric}"
        encoded_message: bytes = message.encode()
        
        logging.debug(f"Sending routing table to neighbors: {message}")

        # send it to all neighbors
        for neighbor in self.neighbors:
            self.sock_mutex.acquire()
            self.sock.sendto(encoded_message, (neighbor, PORT))
            self.sock_mutex.release()

    def _announce_entry(self) -> None:
        """
        Announces to the neighbors that this router has entered the network.
        """
        logging.debug(f"Announcing entry to neighbors: {self.neighbors}")

        encoded_message: bytes = f"@{self.my_ip}".encode()
        for neighbor in self.neighbors:
            self.sock_mutex.acquire()
            self.sock.sendto(encoded_message, (neighbor, PORT))
            self.sock_mutex.release()

    def _handle_incoming_messages(self) -> None:
        """
        Monitors the router's socket for incoming messages and handles them.
        This should be called in a new thread; otherwise, it will block the caller.
        """
        table_regex      = re.compile(ROUTING_TABLE_PATTERN)
        announce_regex   = re.compile(ROUTING_ANNOUNCEMENT_PATTERN)
        plain_text_regex = re.compile(PLAIN_TEXT_PATTERN)

        while True:
            self.sock_mutex.acquire()
            data, addr = self.sock.recvfrom(1024)
            self.sock_mutex.release()
            
            sender_ip: str = addr[0]
            message:   str = data.decode()

            if table_regex.match(message):
                logging.debug(f"Received routing table from {sender_ip}: {message}")
                for entry in table_regex.findall(message):
                    self.table_mutex.acquire()
                    self._update_table(sender_ip, entry)
                    self.table_mutex.release()
            elif announce_regex.match(message):
                logging.debug(f"Received entry announcement from {sender_ip}: {message}")
                # Add the neighbor to the routing table
                self.table_mutex.acquire()
                self.table[message[1:]] = Path(message[1:], 1)
                self.table_mutex.release()
            elif plain_text_regex.match(message):
                logging.debug(f"Received plain text message from {sender_ip}")
                matches = plain_text_regex.findall(message)[0]
                self.table_mutex.acquire()
                self._handle_plain_text_message(matches[0], matches[1], matches[2])
                self.table_mutex.release()
            else:
                logging.debug(f"Discarding unrecognized message from {sender_ip}: {message}")
            
    def _update_table(self, out_ip: str, entry: str) -> None:
        """
        Checks if the routing table already includes the given entry. If not, it is added. If it does, then it will be updated
        if the new metric is lower than or equal to the current one.
        Args:
            sender_ip (str): The IP address of the sender of the routing table.
            entry (str): The entry to be added or updated in the routing table. It should be in the format '192.168.10.1:1'.
        """
        logging.debug(f"Updating (or not) routing table with entry: {entry}")

        if out_ip == self.my_ip:
            logging.debug("Ignoring route to myself")
            return

        data: List[str] = entry.split(":")
        route_ip: str = data[0]
        metric: int = int(data[1])

        if route_ip not in self.table:
            logging.debug("Adding new entry to routing table")
            self.table[route_ip] = Path(out_ip, metric)
            self._send_routes() # need to send the routing table whenever it changes
            return
        
        # Update existing entry
        # When the new metric is the same as the old one, favor updating the entry so
        # the timestamp is reset and the route is not considered stale
        if metric <= self.table[route_ip].metric:
            logging.debug("Updating existing entry in routing table")
            self.table[route_ip].metric = metric
            self.table[route_ip].timestamp = time.time()
            self._send_routes() # need to send the routing table whenever it changes

    def _handle_plain_text_message(self, sender_ip: str, dest_ip: str, text: str) -> None:
        """
        Handles a plain text message. If it is a message for this router, it will be printed. Otherwise, it will be forwarded.
        Args:
            sender_ip (str): The IP address of the sender of the message.
            dest_ip (str): The IP address of the destination of the message.
            text (str): The message to be sent.
        """
        if dest_ip == self.my_ip:
            # Message is for me
            logging.info(f"Message from {sender_ip}: '{text}'")
            return
        
        # Message is not for me - is it in the routing table?
        if dest_ip not in self.table:
            # No route to the destination
            logging.error(f"No route to {dest_ip}")
            return
        
        # Message is not for me - forward it
        logging.debug(f"Forwarding message from {sender_ip} to {dest_ip}")
        next_hop: str = self.table[dest_ip].out_address
        self.sock_mutex.acquire()
        self.sock.sendto(text.encode(), (next_hop, PORT))
        self.sock_mutex.release()

    def _remove_stale_routes(self) -> None:
        """
        Removes stale routes from the router's routing table.
        """
        for dest_ip, path in self.table.items():
            if time.time() - path.timestamp > STALE_ROUTE_THRESHOLD:
                logging.debug(f"Removing stale route to {dest_ip}")
                self.table_mutex.acquire()
                del self.table[dest_ip]
                self.table_mutex.release()

    def _handle_outgoing_message(self) -> None:
        """
        Monitors the terminal for user input and sends plain text messages.
        This should be called in a new thread; otherwise, it will block the caller.
        """
        while True:
            msg:     str = input("Enter the message you want to send: ")
            dest_ip: str = input("Enter the destination IP: ")

            self.table_mutex.acquire()
            if dest_ip not in self.table:
                logging.error(f"No route to {dest_ip}. Giving up...")
                continue
            
            logging.debug(f"Sending message to {dest_ip}: {msg}")

            next_hop: str = self.table[dest_ip].out_address
            self.table_mutex.release()

            self.sock_mutex.acquire()
            self.sock.sendto(f"&{self.my_ip}%{dest_ip}%{msg}".encode(), (next_hop, PORT))
            self.sock_mutex.release()

    def run(self) -> None:
        """
        Runs the router.
        """
        self._announce_entry()
        schedule.every(SEND_ROUTES_INTERVAL).seconds.do(self._send_routes)
        schedule.every(1).seconds.do(self._remove_stale_routes) # check for stale routes every second
        schedule.every(SHOW_ROUTING_TABLE_INTERVAL).seconds.do(self._show_routing_table)
        threading.Thread(target=self._handle_incoming_messages).start()
        threading.Thread(target=self._handle_outgoing_message).start()
        while True:
            schedule.run_pending()
            time.sleep(1)

if __name__ == "__main__":
    # parse command line arguments
    parser = argparse.ArgumentParser(description='Router application')
    parser.add_argument(
        '-v',
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    args = parser.parse_args()

    # set up logging
    logging.basicConfig(
        filename=OUTPUT_FILE,
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(levelname)s - %(asctime)s (at %(funcName)s:%(lineno)d): %(message)s\n',
        datefmt='%H:%M:%S',
        filemode='a'
    )

    # start the router
    Router().run()