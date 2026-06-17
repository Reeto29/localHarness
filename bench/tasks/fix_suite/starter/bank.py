class Account:
    def __init__(self, balance=0):
        self.balance = balance

    def deposit(self, amount):
        # bug: subtracts instead of adds
        self.balance -= amount

    def withdraw(self, amount):
        # bug: no overdraft check
        self.balance -= amount

    def transfer(self, other, amount):
        # bug: credits other but never debits self
        other.balance += amount
