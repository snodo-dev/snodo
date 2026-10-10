"""Validator package — built-ins register after the registry is initialized.

Keep package import side-effect free: importing ``snodo.validators.registry``
first must not eagerly import built-ins that re-enter the partially initialized
registry module.
"""
