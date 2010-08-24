#!/usr/bin/env python
"""
_New_
MySQL implementation of JobGroup.New
"""
__all__ = []
__revision__ = "$Id: New.py,v 1.3 2008/10/01 22:10:05 metson Exp $"
__version__ = "$Revision: 1.3 $"

from WMCore.Database.DBFormatter import DBFormatter
from WMCore.Services.UUID import makeUUID

class New(DBFormatter):
    sql = []
    sql.append("insert into wmbs_jobgroup (subscription, uid) values (:subscription, :uid)")
    sql.append("""select id from wmbs_jobgroup where uid=:uid""")
    def format(self, result, uid):
        result = DBFormatter.format(self, result)
        result = result[0][0]
        return result, uid
        
    def execute(self, subscription=None, conn = None, transaction = False):
        uid=makeUUID()
        binds = self.getBinds(subscription=subscription, uid=uid)
        self.logger.debug('JobGroup.New sql: %s' % self.sql)
        self.logger.debug('JobGroup.New binds: %s' % binds)
        result = self.dbi.processData(self.sql[0], binds)
        binds = self.getBinds(uid=uid)
        result = self.dbi.processData(self.sql[1], binds)
        return self.format(result, uid)