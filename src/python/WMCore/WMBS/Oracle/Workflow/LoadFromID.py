#!/usr/bin/env python
"""
_LoadFromID_

Oracle implementation of Workflow.LoadFromID

"""
__all__ = []
__revision__ = "$Id: LoadFromID.py,v 1.2 2008/11/24 21:51:56 sryu Exp $"
__version__ = "$Revision: 1.2 $"

from WMCore.WMBS.MySQL.Workflow.LoadFromID import LoadFromID as LoadWorkflowMySQL

class LoadFromID(LoadWorkflowMySQL):
    sql = LoadWorkflowMySQL.sql 