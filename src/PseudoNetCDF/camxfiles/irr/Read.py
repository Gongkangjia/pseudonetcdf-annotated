__all__=['irr']
__doc__ = """
.. _Read
:mod:`Read` -- irr Read interface
============================================

.. module:: Read
   :platform: Unix, Windows
   :synopsis: Provides :ref:`PseudoNetCDF` random access read for CAMx
              irr files.  See PseudoNetCDF.sci_var.PseudoNetCDFFile 
              for interface details
.. moduleauthor:: Barron Henderson <barronh@unc.edu>
"""
HeadURL="$HeadURL: http://dawes.sph.unc.edu:8080/uncaqmlsvn/pyPA/utils/trunk/CAMxRead.py $"
ChangeDate = "$LastChangedDate$"
RevisionNum= "$LastChangedRevision$"
ChangedBy  = "$LastChangedBy: svnbarronh $"
__version__ = RevisionNum

#Distribution packages
from datetime import datetime, timedelta
from types import GeneratorType
import unittest
import struct,sys,os,operator
from warnings import warn
from tempfile import TemporaryFile as tempfile
from math import ceil
import os,sys

#Site-Packages
from numpy import zeros,array,where,memmap,newaxis,dtype
from PseudoNetCDF.netcdf import NetCDFFile as ncf

#This Package modules
from PseudoNetCDF.camxfiles.util import sliceit
from PseudoNetCDF.camxfiles.FortranFileUtil import OpenRecordFile,read_into,Int2Asc,Asc2Int
from PseudoNetCDF.sci_var import PseudoNetCDFFile, PseudoNetCDFVariable, PseudoNetCDFVariables


#for use in identifying uncaught nan
listnan=struct.unpack('>f','\xff\xc0\x00\x00')[0]
checkarray=zeros((1,),'f')
checkarray[0]=listnan
array_nan=checkarray[0]

class irr(PseudoNetCDFFile):
    """
    irr provides a PseudoNetCDF interface for CAMx
    irr files.  Where possible, the inteface follows
    IOAPI conventions (see www.baronams.com).
    
    ex:
        >>> irr_path = 'camx_irr.bin'
        >>> rows,cols = 65,83
        >>> irrfile = irr(irr_path,rows,cols)
        >>> irrfile.variables.keys()
        ['TFLAG', 'RXN_01', 'RXN_02', 'RXN_03', ...]
        >>> v = irrfile.variables['RXN_01']
        >>> tflag = irrfile.variables['TFLAG']
        >>> tflag.dimensions
        ('TSTEP', 'VAR', 'DATE-TIME')
        >>> tflag[0,0,:]
        array([2005185,       0])
        >>> tflag[-1,0,:]
        array([2005185,  240000])
        >>> v.dimensions
        ('TSTEP', 'LAY', 'ROW', 'COL')
        >>> v.shape
        (25, 28, 65, 83)
        >>> irrfile.dimensions
        {'TSTEP': 25, 'LAY': 28, 'ROW': 65, 'COL': 83}
    """
    
    id_fmt="ifiiiii"
    data_fmt="f"
    def __init__(self,rf,units='umol/hr',conva=None):
        """
        Initialization included reading the header and learning
        about the format.
        
        see __readheader and __gettimestep() for more info
        """
        self.rffile=OpenRecordFile(rf)
        self.rffile.infile.seek(0,2)
        if self.rffile.infile.tell()<2147483648L:
            warn("For greater speed on files <2GB use ipr_memmap")
        self.rffile.infile.seek(0,0)
        self.__readheader()
        self.__gettimestep()
        self.units=units
        #__conv is a conversion array that comes from ipr
        #if units is not umol/hr, conv must be provided
        self.__conv=conva
        if self.units!='umol/hr' and self.__conv==None:
            raise ValueError, "When units are provided, a conversion array dim(t,z,x,y) must also be provided"
        varkeys=['IRR_%d' % i for i in range(1,self.nrxns+1)]

        domain=self.padomains[0]
        self.dimensions=dict(TSTEP=self.time_step_count,COL=domain['iend']-domain['istart']+1,ROW=domain['jend']-domain['jstart']+1,LAY=domain['tlay']-domain['blay']+1)
        self.createDimension('DATE-TIME', 2)
        self.createDimension('VAR', self.NRXNS)
        self.variables=PseudoNetCDFVariables(self.__var_get,varkeys)
        tflag = self.createVariable('TFLAG', 'i', ('TSTEP', 'VAR', 'DATE-TIME'))
        tflag.units = '<YYYYJJJ, HHMMSS>'
        tflag.var_desc = tflag.long_name = 'TFLAG'.ljust(16)
        tflag[:] = array(self.timerange(), dtype = 'i').reshape(self.NSTEPS, 1, 2)

    def __var_get(self,key):
        rxni = int(key.split('_')[1])
        self.loadVars(rxni, 30)
        return self.variables[key]

    def __readheader(self):
        """
        __readheader reads the header section of the ipr file
        it initializes each header field (see CAMx Users Manual for a list)
        as properties of the ipr class
        """
        self.runmessage=self.rffile.read("80s")
        self.start_date,self.start_time,self.end_date,self.end_time=self.rffile.read("ifif")
        self.SDATE=self.start_date
        self.STIME=self.start_time
        
        self.grids=[]
        for grid in range(self.rffile.read("i")[-1]):
            self.grids.append(
                            dict(
                                zip(
                                    ['orgx','orgy','ncol','nrow','xsize','ysize','iutm'], 
                                    self.rffile.read("iiiiiii")
                                    )
                                )
                            )
        
        self.padomains=[]
        for padomain in range(self.rffile.read("i")[-1]):
            self.padomains.append(
                                dict(
                                    zip(
                                        ['grid','istart','iend','jstart','jend','blay','tlay'],
                                        self.rffile.read("iiiiiii")
                                        )
                                    )
                                )
        self.NRXNS = self.nrxns=self.rffile.read('i')[-1]
        
        self.data_start_byte=self.rffile.record_start
        self.record_fmt=self.id_fmt + str(self.nrxns) + self.data_fmt
        self.record_dtype = dtype(dict(names = 'SPAD DATE TIME PAGRID NEST I J K'.split() + ['IRR_%d' % rxn for rxn in range(1, self.NRXNS + 1)] + ['EPAD'],
                                  formats = ['i'] + list(self.id_fmt) + self.NRXNS * [self.data_fmt] + ['i']))
        self.record_size=self.rffile.record_size
        self.padded_size=self.record_size+8
        
    def __gettimestep(self):
        """
        Header information provides start and end date, but does not
        indicate the increment between.  This routine reads the first
        and second date/time and initializes variables indicating the
        timestep length and the anticipated number.
        """
        self.activedomain=self.padomains[0]
        self.rffile._newrecord(
                        self.data_start_byte+(
                                    self.__jrecords(0,self.padomains[0]['jend'])*
                                    self.padded_size
                                    )
                        )
        date,time=self.rffile.read("if")
        tstart = datetime.strptime('%05dT%04d' % (self.SDATE, self.STIME), '%y%jT%H%M')
        tone = datetime.strptime('%05dT%04d' % (date, time), '%y%jT%H%M')
        tstep = tone - tstart
        self.time_step = self.TSTEP = int((datetime.strptime('0000', '%H%M') + tstep).strftime('%H%M'))
        self.EDATE = self.end_date
        self.SDATE = self.start_date
        self.STIME = self.start_time
        self.ETIME = self.end_time
        tend = datetime.strptime('%05dT%04d' % (self.EDATE, self.ETIME), '%y%jT%H%M')
        tdiff = tend - tstart
        multiple = (tdiff.days * 24 * 3600. + tdiff.seconds) / (tstep.days * 24 * 3600. + tstep.seconds)
        self.NSTEPS = self.time_step_count = int(multiple)
        assert(multiple == int(multiple))

    def __gridrecords(self,pagrid):
        """
        routine returns the number of records to increment from the
        data start byte to find the pagrid
        """
        ntime=self.__timerecords(pagrid,(self.end_date,int(self.end_time+self.time_step)))
        return ntime
        
    def __timerecords(self,pagrid,(d,t)):
        """
        routine returns the number of records to increment from the
        data start byte to find the first time
        """
        nsteps=self.timerange().index((d,t))
        nj=self.__jrecords(pagrid,self.padomains[pagrid]['jend']+1)
        return nsteps*nj
        
    def __irecords(self,pagrid,i):
        """
        routine returns the number of records to increment from the
        data start byte to find the first icell
        """
        ni=i-self.activedomain['istart']
        nk=self.__krecords(pagrid,self.activedomain['tlay']+1)
        return ni*nk
        
    def __jrecords(self,pagrid,j):
        """
        routine returns the number of records to increment from the
        data start byte to find the first jcell
        """
        nj=j-self.activedomain['jstart']
        ni=self.__irecords(pagrid,self.activedomain['iend']+1)
        return nj*ni
        
    def __krecords(self,pagrid,k):
        """
        routine returns the number of records to increment from the
        data start byte to find the first kcell
        """
        return k-self.activedomain['blay']

    def __recordposition(self,pagrid,date,time,i,j,k):
        """ 
        routine uses pagridrecords, timerecords,irecords,
        jrecords, and krecords multiplied by the fortran padded size
        to return the byte position of the specified record
        
        pagrid - integer
        date - integer
        time - float
        i - integer
        j - integer
        k - integer
        """
        records=0
        for pag in range(pagrid):
            records+=__gridrecords(pag)
        records+=self.__timerecords(pagrid,(date,time))
        records+=self.__jrecords(pagrid,j)
        records+=self.__irecords(pagrid,i)
        records+=self.__krecords(pagrid,k)
        return records*self.padded_size+self.data_start_byte
        
    def seek(self,pagrid=1,date=None,time=None,i=1,j=1,k=1):
        """
        Move file cursor to beginning of specified record
        see __recordposition for a definition of variables
        """
        if date==None:
            date=self.start_date
        if time==None:
            time=self.start_time+self.TSTEP
        self.activedomain=self.padomains[pagrid]
        self.rffile._newrecord(self.__recordposition(pagrid,date,time,i,j,k))
    
    def read(self):
        """
        provide direct access to the underlying RecordFile read
        method
        """
        return self.rffile.read(self.record_fmt)
    
    def read_into(self,dest):
        """
        put values from rffile read into dest
        dest - numpy or numeric array
        """
        return read_into(self.rffile,dest,self.id_fmt,self.data_fmt)
    
    def seekandreadinto(self,dest,pagrid=1,date=None,time=None,i=1,j=1,k=1):
        """
        see seek and read_into
        """
        self.seek(pagrid,date,time,i,j,k)
        return self.read_into(dest)
    
    def seekandread(self,pagrid=1,date=None,time=None,i=1,j=1,k=1):
        """
        see seek and read
        """
        self.seek(pagrid,date,time,i,j,k)
        return self.read()

    def iteritems(self,pagrid=0):
        for pagrid,d,t,i,j,k in self.iterkeys(pagrid):
            return pagrid,d,t,i,j,k,self.seekandread(pagrid,d,t,i,j,k)
 
    def itervalues(self,pagrid=0):
        for pagrid,d,t,i,j,k in self.iterkeys(pagrid):
            return self.seekandread(pagrid,d,t,i,j,k)
    
    def iterkeys(self,pagrid=0):
        domain=self.padomains[pagrid]
        for d,t in self.timerange():
            for i in range(domain['istart'],domain['iend']):
                for j in range(domain['jstart'],domain['jend']):
                    for k in range(domain['kstart'],domain['kend']):
                        yield pagrid,d,t,i,j,k
                         
    def loadVars(self,start, n, pagrid=0):
        domain=self.padomains[pagrid]
        istart=domain['istart']
        iend=domain['iend']
        jstart=domain['jstart']
        jend=domain['jend']
        kstart=domain['blay']
        kend=domain['tlay']
        variables = self.variables
        temp = zeros((self.nrxns,), 'f')
        shape = (self.NSTEPS,) + eval('(LAY, ROW, COL)', None, self.dimensions)
        variables.clear()
        end = min(start + n, self.NRXNS + 1)
        for rxn in range(start, end):
            key = 'IRR_%d' % rxn
            variables[key] = PseudoNetCDFVariable(self, key, 'f', ('TSTEP', 'LAY', 'ROW', 'COL'), values = zeros(shape, 'f'), units = 'ppm/hr', var_desk = key.ljust(16), long_name = key.ljust(16))

        self.seek(pagrid = 0, i = istart, j = jstart, k = kstart)
        for ti, (d,t) in enumerate(self.timerange()):
            for ji, j in enumerate(range(jstart, jend+1)):
                for ii, i in enumerate(range(istart, iend+1)):
                    for ki, k in enumerate(range(kstart, kend+1)):
                        date, time, pad, nest, id, jd, kd = self.read_into(temp)
                        assert(id == i)
                        assert(jd == j)
                        assert(kd == k)
                        assert(date == d)
                        assert(time == t)
                        self.rffile.infile.seek(8, 1)
                        for rxn in range(start, end):
                            variables['IRR_%d' % rxn][ti, ki, ji, ii] = temp[rxn-1]
            
    def timerange(self):
        tstart = datetime.strptime('%05dT%04d' % (self.SDATE, self.STIME), '%y%jT%H%M')
        tdiff = datetime.strptime('%04d' % self.TSTEP, '%H%M') - datetime.strptime('0000', '%H%M')
        dates = [tstart + (tdiff * i) for i in range(1, self.NSTEPS+1)]
        return [(int(d.strftime('%y%j')), float(d.strftime('%H%M'))) for d in dates]

class TestRead(unittest.TestCase):
    def runTest(self):
        pass
    def setUp(self):
        pass
        
    def testIRR(self):
        emissfile=irr('../../../../testdata/ei/camx_cb4_ei_lo.20000825.hgb8h.base1b.psito2n2.hgbpa_04km')
        self.assert_(1==2)
       
if __name__ == '__main__':
    unittest.main()
