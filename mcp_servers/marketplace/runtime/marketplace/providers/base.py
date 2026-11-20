"""The same interface for every marketplace implementation."""
from abc import ABC,abstractmethod

class Provider(ABC):
    name:str
    @abstractmethod
    def search(self,query,limit=12):pass
    @abstractmethod
    def product(self,ref,variant_limit=0,quantity=1,city=None,images_dir=None):pass
    @abstractmethod
    def quote(self,ref,quantity=1,city=None):pass
    @abstractmethod
    def images(self,ref,path,limit=12):pass
    @abstractmethod
    def cart_list(self):pass
    @abstractmethod
    def cart_set(self,ref,quantity,max_unit_price,operation_id,expected_quantity=None,expected_options=None):pass
    @abstractmethod
    def orders_list(self,active_only=True,max_orders=30,max_pages=5):pass
    @abstractmethod
    def order_get(self,order_id):pass
    @abstractmethod
    def order_code(self,order_id,path):pass
    @abstractmethod
    def session(self,action,profile=None):pass
