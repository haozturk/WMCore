#!/usr/bin/env python
"""
    WorkQueue tests
"""

__revision__ = "$Id: WorkQueue_t.py,v 1.3 2009/06/19 14:19:40 swakef Exp $"
__version__ = "$Revision: 1.3 $"

import unittest
import pickle
import os
from WMCore.WorkQueue.WorkQueue import WorkQueue, _WQElement
from WMCore.WMSpec.WMWorkload import newWorkload
from WMCore.WMSpec.WMTask import makeWMTask
from WMCore.WorkQueue.WorkSpecParser import WorkSpecParser


def createSpec(name, path, dataset = None):
    """
    create a wmspec object and save to disk
    """
    wmspec = newWorkload(name)
    task = makeWMTask('task1')
    if dataset:
        task.data.parameters.inputDatasets = dataset
        task.data.parameters.splitType = 'File'
        task.data.parameters.splitSize = 1
        task.data.constraints.sites.blacklist = ['SiteA']
        wmspec.data.dbs = 'http://example.com'
    else:
        task.data.parameters.splitType = 'Event'
        task.data.parameters.splitSize = 100
        task.data.parameters.totalEvents = 1000
    wmspec.addTask(task)
    out = open(path, 'wb')
    pickle.dump(wmspec, out)
    out.close()


# //  mock dbs info - ignore a lot of arguments
#//     - ignore some params in dbs spec - silence pylint warnings
# pylint: disable-msg=W0613,R0201
class MockDBSReader:
    """
    Mock up dbs access
    """
    def __init__(self, url):
        self.blocks = {'/fake/test/RAW': [{'Name' : '/fake/test/RAW#1',
                                    'NumEvents' : 500,
                                    'NumFiles' : 5,
                                    'Parents' : ()},
                                    {'Name' : '/fake/test/RAW#2',
                                    'NumEvents' : 1000,
                                    'NumFiles' : 10,
                                    'Parents' : ()}
                                    ]}
        self.locations = {'/fake/test/RAW#1' : ['SiteA'],
                '/fake/test/RAW#2' : ['SiteA', 'SiteB']}
    
    def getFileBlocksInfo(self, dataset, onlyClosedBlocks=True):
        """Fake block info"""
        return self.blocks[dataset]
    
    def listFileBlockLocation(self, block):
        """Fake locations"""
        return self.locations[block]
# pylint: enable-msg=W0613,R0201
        


class WorkQueueElementTest(unittest.TestCase):
    """
    _WorkQueueElementTest_
    
    """
    def setUp(self):
        """
        If we dont have a wmspec file create one
        """
        self.specFile = os.path.join(os.getcwd(), 'testworkflow.pickle')
        self.specName = 'testWf'
        createSpec(self.specName, self.specFile)
        self.specHelper = WorkSpecParser(self.specFile)


    def tearDown(self):
        """tearDown"""
        try:
            os.unlink(self.specFile)
        except OSError:
            pass


    def testOrdering(self):
        """
        Test priority sorting
        """
        ele1 = _WQElement(self.specHelper, 1)
        ele2 = _WQElement(self.specHelper, 1)
        ele2.priority = 2
        self.assertTrue(ele2 < ele1)
        ele1.priority = 3
        self.assertTrue(ele1 < ele2)
        # ensure old jobs rise in priority - very basic check
        ele2.insert_time = 0
        self.assertTrue(ele2 < ele1)


    def testMatch(self):
        """
        Test elements match correctly
        """
        condition = {'SiteA' : 100}
        ele = _WQElement(self.specHelper, 50)
        matched, _ = ele.match({'SiteA' : 49})
        self.assertFalse(matched)
        matched, condition = ele.match(condition)
        self.assertTrue(matched)
        self.assertEqual(condition, {'SiteA' : 50})
        matched, condition = ele.match(condition)
        self.assertTrue(matched)
        self.assertEqual(condition, {})
        matched, condition = ele.match(condition)
        self.assertFalse(matched)
        ele.setStatus("Acquired")
        self.assertEqual("Acquired", ele.status)


    def runTest(self):
        """Run all tests"""
        self.testOrdering() 
        self.testMatch()


class WorkQueueTest(unittest.TestCase):
    """
    _WorkQueueTest_
    
    """
    setup = False
#    specFile = os.path.join(os.getcwd(), 'testworkflow.pickle')
#    specName = 'testWf'
    queue = None
    
    
    def setUp(self):
        """
        If we dont have a wmspec file create one
        """
        self.specFile = os.path.join(os.getcwd(), 'testworkflow.pickle')
        self.specName = 'testWf'
        createSpec(self.specName, self.specFile)
        self.processingSpecName = 'testProcessing'
        self.processingSpecFile = os.path.join(os.getcwd(), self.processingSpecName + ".pckl")
        createSpec(self.processingSpecName, self.processingSpecFile, ['/fake/test/RAW'])
        #if not self.__class__.queue:
        self.__class__.queue = WorkQueue()
        mockDBS = MockDBSReader('http://example.com')
        self.__class__.queue.dbsHelpers['http://example.com'] = mockDBS
        self.setup = True


    def tearDown(self):
        """tearDown"""
        for f in (self.specFile, self.processingSpecFile):
            try:
                os.unlink(f)
            except OSError:
                pass


    def testProduction(self):
        """
        Create and enqueue a production WMSpec.
        
        Test enqueing, priority change and work acquire
        """
        specfile = self.specFile
        numBlocks = 2
        njobs = [10] * numBlocks # array of jobs per block
        total = sum(njobs)
        
        # Queue Work & check accepted
        for _ in range (0, numBlocks):
            self.queue.queueWork(specfile)
        self.assertEqual(numBlocks, len(self.queue))
        
        # try to get work - Note hardcoded values - bah.
        work = self.queue.getWork({'SiteA' : 0})
        self.assertEqual([], work)
        work = self.queue.getWork({'SiteA' : njobs[0]})
        self.assertEqual(len(work), 1)
        work = self.queue.getWork({'SiteA' : njobs[0], 'SiteB' : njobs[1]})
        self.assertEqual(len(work), 2)
        
        # priority change
        affectedBlocks = self.queue.setPriority(50, self.specFile)
        self.assertNotEqual(0, affectedBlocks)
        affectedBlocks = self.queue.setPriority(50, 'blahhhhhh')
        self.assertFalse(affectedBlocks)
        
        # check work still available if not claimed
        work = self.queue.getWork({'SiteA' : 10000})
        self.assertEqual(len(work), numBlocks)
        
        # claim all work
        work = self.queue.getWork({'SiteA' : total})
        self.assertEqual(len(work), numBlocks)
        gotWork = self.queue.gotWork(*work)
        self.assertTrue(gotWork)
        
        #no more work available
        self.assertEqual(0, len(self.queue.getWork({'SiteA' : total})))


    def testProcessing(self):
        """
        Create and enqueue a processing WMSpec
        
        Test enqueue and location features
        """
        specfile = self.processingSpecFile
        njobs = [5, 10] # array of jobs per block
        numBlocks = len(njobs)
        total = sum(njobs)
        
        # Queue Work & check accepted
        self.queue.queueWork(specfile)
        self.assertEqual(numBlocks, len(self.queue))

        # Check splitting
        #In blacklist
        work = self.queue.getWork({'SiteA' : total})
        self.assertEqual(len(work), 0)
        # Not quite enough resources
        work = self.queue.getWork({'SiteA' : njobs[0]-1, 'SiteB' : njobs[1]-1})
        self.assertEqual(len(work), 0)
        # Only 1 block at SiteB
        work = self.queue.getWork({'SiteB' : total})
        self.assertEqual(len(work), 1)
        # 1st block cant run anywhere
        work = self.queue.getWork({'SiteA' : total, 'SiteB' : total})
        self.assertEqual(len(work), 1)
        
        # update locations - put block1 at SiteA & SiteB
        self.__class__.queue.dbsHelpers['http://example.com'].locations['/fake/test/RAW#1'] = ['SiteA', 'SiteB']
        self.queue.updateLocationInfo()
        # SiteA still blacklisted for all blocks
        work = self.queue.getWork({'SiteA' : total})
        self.assertEqual(len(work), 0)
        # SiteB can run all blocks now
        work = self.queue.getWork({'SiteB' : total})
        self.assertEqual(len(work), 2)
        

#    def testRestore(self):
#        """
#        Create a WorkQueue destroy it and restore
#        """
#        specfile = self.specFile
#        numBlocks = 2
#        
#        # Queue Work & check accepted
#        for _ in range (0, numBlocks):
#            self.queue.queueWork(specfile)
#        self.assertEqual(numBlocks, len(self.queue))
#        
#        store = []
#        for ele in self.queue.elements:
#            store.append(WorkUnit(ele.primaryBlock,
#                                  ele.blockLocations.keys(),
#                                  ele.nJobs))
#
#        # destroy queue
#        self.queue.elements = []
#        # reload
#        
#        #verify
#        self.assertEqual(numBlocks, len(self.queue))
#        

    def runTest(self):
        """run all tests"""
        self.testProduction() 
        self.testProcessing()


if __name__ == "__main__":
    unittest.main()