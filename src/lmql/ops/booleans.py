from .node import *
from lmql.ops.max_token_hints import *

class NotOp(Node):
    def forward(self, op, **kwargs):
        return not op

    def follow(self, v, **kwargs):
        return not v

class OrOp(Node):
    def forward(self, *args, **kwargs):
        if True in args:
            return True
        elif all(a == False for a in args):
            return False
        else:
            return None

    def follow(self, *args, **kwargs):
        return fmap(
            ("*", self.forward(*args))
        )

    def final(self, args, operands=None, result=None, **kwargs):
        if result:
            return (
                "fin"
                if any(a == "fin" and v == True for a, v in zip(args, operands))
                else "var"
            )
        return "var" if any(a == "var" for a in args) else "fin"

class AndOp(Node):
    def forward(self, *args, **kwargs):
        if type(args[0]) is tuple and len(args) == 1:
            args = args[0]

        if any(a == False for a in args):
            return False
        elif any(a is None for a in args):
            return None
        else:
            return all(list(args))

    def follow(self, *v, **kwargs):
        return fmap(
            ("*", self.forward(*v))
        )

    def final(self, args, operands=None, result=None, **kwargs):
        if result:
            if all(a == "fin" for a in args):
                return "fin"
        elif any(a == "fin" and v == False for a, v in zip(args, operands)):
            return "fin"
        return "var"
        
    @staticmethod
    def all(*args):
        if not args:
            return None
        elif len(args) == 1:
            return args[0]
        else:
            return AndOp([AndOp.all(*args[:-1]), args[-1]])
        
    def token_hint(self):
        operand_hints = [op.token_hint() for op in self.predecessors]
        return dict_min_token_hint(operand_hints)