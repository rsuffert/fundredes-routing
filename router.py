from typing import List, Dict, Tuple
import schedule
import time
import socket
import threading

NEIGHBORS_FILE: str = "roteadores.txt"
PORT: int = 19_000

# These are expressed IN SECONDS
SEND_ROUTES_INTERVAL:         int = 15
STALE_ROUTE_THRESHOLD:        int = 35
SHOW_ROUTING_TABLE_INTERVAL:  int = 10

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
        # load neighbors and my IP
        my_ip, neighbors = self._load_neighbors()
        self.neighbors: List[str] = neighbors
        self.my_ip: str = my_ip

        # build initial routing table
        self.table: Dict[str, Path] = {}
        self.table_mutex = threading.Lock()
        for neighbor in self.neighbors:
            self.table_mutex.acquire()
            self.table[neighbor] = Path(neighbor, 1)
            self.table_mutex.release()
        
        # create UDP socket
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
        my_ip: str = ""
        with open(NEIGHBORS_FILE, 'r') as f:
            my_ip = f.readline().strip()
            for ip in f:
                neighbors.append(ip.strip())
        return my_ip, neighbors

    def _show_routing_table(self) -> None:
        """
        Prints the routing table of the router instance in a formatted way.
        """
        print(f"{'Destination IP':<20} {'Metric':<10} {'Output IP':<20}")
        print("="*50)
        for dest_ip, path in self.table.items():
            print(f"{dest_ip:<20} {path.metric:<10} {path.out_address:<20}")

    def _send_routes(self) -> None:
        """
        Sends the routing table to all neighbors.
        """
        # parse the routing table into the required format
        message: str = ""
        for dest_ip, path in self.table.items():
            message += f"!{path.out_address}:{path.metric}"
        encoded_message: bytes = message.encode()

        # send it to all neighbors
        for neighbor in self.neighbors:
            self.sock_mutex.acquire()
            self.sock.sendto(encoded_message, (neighbor, PORT))
            self.sock_mutex.release()

    def _announce_entry(self) -> None:
        """
        Announces to the neighbors that this router has entered the network.
        """
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
        # TODO: implement this
        pass

    def _remove_stale_routes(self) -> None:
        """
        Removes stale routes from the router's routing table.
        """
        for dest_ip, path in self.table.items():
            if time.time() - path.timestamp > STALE_ROUTE_THRESHOLD:
                self.table_mutex.acquire()
                del self.table[dest_ip]
                self.table_mutex.release()

    def run(self) -> None:
        """
        Runs the router.
        """
        self._announce_entry()
        schedule.every(SEND_ROUTES_INTERVAL).seconds.do(self._send_routes)
        schedule.every(1).seconds.do(self._remove_stale_routes) # check for stale routes every second
        schedule.every(SHOW_ROUTING_TABLE_INTERVAL).seconds.do(self._show_routing_table)
        threading.Thread(target=self._handle_incoming_messages).start()
        while True:
            schedule.run_pending()
            time.sleep(1)