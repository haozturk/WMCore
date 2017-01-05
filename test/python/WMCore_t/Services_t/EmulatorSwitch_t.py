'''
Created on Oct 4, 2011

@author: meloam
'''
import unittest

from WMCore.Services.EmulatorSwitch import EmulatorHelper


class EmulatorSwitch_t(unittest.TestCase):
    def testGetEmulators(self):
        from WMCore.Services.PhEDEx.PhEDEx import PhEDEx
        phedexJSON = PhEDEx(responseType='json')
        self.assertTrue(hasattr(phedexJSON, '_testNonExistentInEmulator'))
        EmulatorHelper.setEmulators(phedex=True, dbs=False, siteDB=False, requestMgr=False)
        phedexJSON2 = PhEDEx(responseType='json')
        self.assertFalse(hasattr(phedexJSON2, '_testNonExistentInEmulator'))
        EmulatorHelper.resetEmulators()
        phedexJSON2 = PhEDEx(responseType='json')
        self.assertTrue(hasattr(phedexJSON2, '_testNonExistentInEmulator'))

    def tearDown(self):
        EmulatorHelper.resetEmulators()


if __name__ == "__main__":
    # import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
