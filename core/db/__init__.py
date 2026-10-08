"""SQLite 持久化层（现代化 DAO 仓储体系）。"""
import sys
from types import ModuleType
from . import base, accounts, email_pool, jobs, codex, grok, uned, kiro, outlook, github
from .base import *
from .accounts import *
from .email_pool import *
from .jobs import *
from .codex import *
from .grok import *
from .uned import *
from .kiro import *
from .outlook import *
from .github import *

_SUBMODULES = (base, accounts, email_pool, jobs, codex, grok, uned, kiro, outlook, github)



class _DbModule(ModuleType):
    def __init__(self, name: str, doc: str | None = None):
        super().__init__(name, doc)
        for mod in _SUBMODULES:
            for k, v in vars(mod).items():
                if not k.startswith("__"):
                    super().__setattr__(k, v)

    def __getattr__(self, name: str):
        for mod in _SUBMODULES:
            if hasattr(mod, name):
                return getattr(mod, name)
        raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

    def __setattr__(self, name: str, value):
        super().__setattr__(name, value)
        if hasattr(self, "_initialized"):
            for mod in _SUBMODULES:
                if hasattr(mod, name) or name.startswith("_"):
                    setattr(mod, name, value)

    def __delattr__(self, name: str):
        super().__delattr__(name)
        for mod in _SUBMODULES:
            if hasattr(mod, name):
                try:
                    delattr(mod, name)
                except AttributeError:
                    pass


_self = _DbModule(__name__, __doc__)
_self.base = base
_self.accounts = accounts
_self.email_pool = email_pool
_self.jobs = jobs
_self.codex = codex
_self.grok = grok
_self.uned = uned
_self.kiro = kiro
_self.outlook = outlook
_self.github = github
_self._initialized = True
sys.modules[__name__] = _self
