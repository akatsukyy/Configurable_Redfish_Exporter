import yaml
import json
import logging
from re import findall
# import os
from os import path,makedirs
from jinja2 import Template
from jsonpath_ng.ext import parse 
# import aiohttp
import asyncio
from aiohttp import ClientConnectorError, ClientResponseError, BasicAuth, ClientTimeout, ClientSession

def readYAMLTemplate(templateFile):
    config_file_path = path.join(path.dirname(__file__), templateFile)
    if path.isfile(config_file_path):
        with open(templateFile, 'r') as f:
            yamlContent = f.read()
            configData = yaml.safe_load(yamlContent)
            logging.debug("Component Schema Data: %s" % (configData))
            return configData
    else:
        logging.error("[%s] Can not find: %s" % (serverAddress,templateFile))
        return

def jsonpathCollector(content,expression,output='value'):
    jsonpath_expr = parse(str(expression))
    if output == 'fullpath&value':
        result = {str(match.full_path): match.value for match in jsonpath_expr.find(content)}
    else:
        result = [match.value for match in jsonpath_expr.find(content)]
        if result == []:
            return False
    return result

def getKeyDictFromURLPath(url, keyDict):
    pathSegment = [segment for segment in url.split("/") if segment]
    dataKeyDict = {key.replace(">>", "", 1): pathSegment[value] for key, value in keyDict.items() if key.startswith(">>")}
    if dataKeyDict == {}:
        return {}
    else:
        return dataKeyDict

def dataJSONWriter(dataRaw,fileDir,fileName, serverAddress):
    if path.isdir(fileDir):
        logging.debug("[%s] Dir %s is existed" % (serverAddress,fileDir))
    else:
        logging.info("[%s] Dir %s is not existed, will create it" % (serverAddress,fileDir))
        makedirs(fileDir, exist_ok=True)
    try:
        with open('%s%s' % (fileDir,fileName), 'w') as file:
            json.dump(dataRaw, file)
        logging.info("[%s] Write data successfully at %s%s" % (serverAddress,fileDir,fileName))
    except Exception as e:
        logging.error("[%s] There error with %s" % (serverAddress,e))
    return

async def fetch(url,username,password, session,serverAddress):
    auth = BasicAuth(username,password)
    retries=3
    backoffFactor=0.5
    for attempt in range(1,retries+1):
        try:
            async with session.get(url,auth=auth, ssl=False) as response:
                return await response.json()
        except (ClientConnectorError, ClientResponseError) as e:
            logging.warning("[%s] Attempt %s with url: %s failed: %s" % (serverAddress,attempt,url,e))
            if attempt == retries:
                logging.error("[%s] Max retries reached. Giving up." % serverAddress)
                raise
            else:
                delay = backoffFactor * (2 ** (attempt - 1))
                logging.warning("[%s] Retrying in %s seconds..." % (serverAddress,delay))
                await asyncio.sleep(delay)        

async def fetch_all(urls: list(),username,password,serverAddress):
    timeout = ClientTimeout(total=180)
    dataRaw = dict()
    try: 
        async with ClientSession(timeout=timeout) as session:
            # if len(urls) == 1:
            #     results = await fetch(urls[0],username,password, session)
            # else:
            tasks = [fetch(url,username,password, session,serverAddress) for url in urls]
            results = await asyncio.gather(*tasks)
            return results
    except Exception as e:
        logging.error("[%s] There error with %s" % (serverAddress,e))

async def rawDataCollector(serverAddress,schemaContent,keyDict: dict,username,password,logLevel):
    # logFormat = '%(asctime)s [%(levelname)s] [' + serverAddress + '] %(message)s'
    # logging.basicConfig(format=logFormat, level=logLevel.upper())
    logging.debug("[%s] Key schemaContent: %s" % (serverAddress,schemaContent))
    logging.debug("[%s] Key ID Dict: %s" % (serverAddress,keyDict))
    if isinstance(schemaContent,dict):
        if '$inituri' in schemaContent:
            # dynamicValue = bool(re.search(r"\{\{\s*[\w]+\s*\}\}", schemaContent['$inituri']))
            dynamicValueList = [match.strip() for match in findall(r"\{\{(.*?)\}\}", schemaContent['$inituri'])]
            logging.debug("[%s] Dynamic Value List from Schema: %s" % (serverAddress,dynamicValueList))
            if dynamicValueList == []:
                uri = schemaContent['$inituri']
            else:
                for dynamicValue in dynamicValueList:
                    if dynamicValue not in keyDict:
                        logging.error("[%s] Can't see dynamic value: %s" % (serverAddress,dynamicValue))
                        return []
                uri = Template(schemaContent['$inituri']).render(keyDict)
        else:
            logging.error("[%s] Can't find $inituri field in schema, please check again")
            return []

        url = "https://%s%s" % (serverAddress,uri)
        logging.debug("[%s] Father URL: %s" % (serverAddress,url))
        # dataRaw = dict()
        if '$jsonpath' in schemaContent:
            tempRaw = (await fetch_all([url],username,password,serverAddress))[0]
            # dataRaw =dataRaw[0]
            childURIList = jsonpathCollector(tempRaw,str(schemaContent['$jsonpath']))
            # logging.info(childURIList)
            if childURIList is False:
                logging.warning("[%s] Child URI List isn't existed" % serverAddress)
                return
            childURLList = ["https://%s%s" % (serverAddress,path) for path in childURIList]
            dataRawList = await fetch_all(childURLList,username,password,serverAddress)
            if dataRawList is None:
                logging.error("[%s] Get data failed" % serverAddress)
                return dataRawList
            for key in schemaContent:
                if isinstance(schemaContent[key],dict):
                    logging.debug("[%s] Found child component: %s" % (serverAddress,key))
                    count = 0
                    for childURL,dataRaw in zip(childURLList,dataRawList):
                        childKey = getKeyDictFromURLPath(childURL, schemaContent)
                        updatedKeyDict = keyDict | childKey
                        logging.debug("[%s] Updated key: %s" % (serverAddress,updatedKeyDict))
                        # keyDict.update(getKeyDictFromURLPath(childURL, schemaContent))
                        try:
                            dataRawList[count][key] = await rawDataCollector(serverAddress,schemaContent[key],updatedKeyDict,username,password,logLevel)
                            count+=1
                        except Exception as e:
                            logging.error("[%s] There error with %s" % (serverAddress,e))
            for childURL,dataRaw in zip(childURLList,dataRawList):
                keyDict.update(getKeyDictFromURLPath(childURL, schemaContent))
        else:
            dataRawList = await fetch_all([url],username,password,serverAddress)
            keyDict.update(getKeyDictFromURLPath(url, schemaContent))
            return dataRawList
        return dataRawList
    else:
        logging.error("[%s] Schema Content isn't dict type, please check again" % serverAddress)
        return []

async def dataCollector(serverAddress,username,password,templateDir,logLevel):
    # logging.getLogger().handlers[0].flush()
    # logFormat = '%(asctime)s [%(levelname)s] %(message)s'  
    # logging.basicConfig(format=logFormat, level=logLevel.upper())

    ### Read schema from schemas/Common.yml file
    endpointURL = "https://%s" % serverAddress
    # auth = (username,password)
    base = templateDir + "schemas/Common.yml"
    # base = templateDir + "schemas/HPEProLiantGen10.yml"
    commonSchema=readYAMLTemplate(base)
    if commonSchema is None:
        logging.error("[%s] Can't generate common schema, please check again" % serverAddress)
        return
    # keyIDDict = {'serverAddress': serverAddress}
    keyIDDict = {}
    logging.debug("[%s] Type of commonSchema %s" % (serverAddress,type(commonSchema)))

    for basePoint in commonSchema['Metadata']:
        # vendorData = await fetch_all(childURIList,username,password)
        vendorData = (await rawDataCollector(serverAddress,commonSchema['Metadata'][basePoint],keyIDDict,username,password,logLevel))[0]

    if 'Manufacturer' in vendorData:
        manufacturer = vendorData['Manufacturer']
        logging.debug("[%s] Manufacturer: %s" % (serverAddress,manufacturer))
    else:
        logging.error("[%s] We can't generate Manufacturer value, Please check JSONPath or else!" % serverAddress)
        return
    
    if 'Model' in vendorData:
        model = vendorData['Model']
        logging.debug("[%s] Model: %s" % (serverAddress,model))
    else:
        logging.error("[%s] We can't generate Model value, Please check JSONPath or else!" % serverAddress)
        return

    if 'Id' in vendorData:
        vendorId = vendorData['Id']
        logging.debug("[%s] VendorId: %s" % (serverAddress,vendorId))
    else:
        logging.error("[%s] We can't generate VendorId value, Please check JSONPath or else!" % serverAddress)
        return

    modelSchema = None
    for i in commonSchema['ModelSchema']:
        if i in manufacturer:
            logging.info("[%s] This's %s Server - Founded Vendor Name %s" % (serverAddress,i,manufacturer))
            for j in commonSchema['ModelSchema'][i]:
                if j in model:
                    logging.info("[%s] Model using %s - Founded Model Name %s" % (serverAddress,j,model))
                    modelSchema = commonSchema['ModelSchema'][i][j]
                    break
                else:
                    logging.debug("[%s] Model isn't %s - Founded Model Name %s" % (serverAddress,j,model))        
        else:
            logging.debug("[%s] This's not %s Server - Founded Vendor Name %s" % (serverAddress,i,manufacturer))
    if modelSchema:
        logging.info("[%s] We will generate data model with schema file: %s" % (serverAddress,modelSchema))
    else:
        logging.error("[%s] We couldn't find any schema similar with server model: %s. Please check schema directory" % (serverAddress,model))
        return

    # logging.info(vendorData)
    modelSchemaDir = templateDir + "schemas/" + modelSchema
    schema=readYAMLTemplate(modelSchemaDir)
    # logging.info(schema)
    dataNewSchema = schema['Data']
    if schema is None:
        logging.error("[%s] Can't generate vendor schema, please check again" % serverAddress)
        return
    data = [rawDataCollector(serverAddress,schema['Metadata'][component],keyIDDict,username,password,logLevel) for component in schema['Metadata']]
    results = await asyncio.gather(*data)
    dataRaw = dict()
    for component, result in zip(schema['Metadata'], results):
        dataRaw[component] = result
    logging.debug("[%s] DataRaw: %s" % (serverAddress,dataRaw))
    fileDir = '/tmp/redfish-data/RawData/'
    fileName = '%s.json' % serverAddress
    dataJSONWriter(dataRaw,fileDir,fileName,serverAddress)
    return dataRaw,dataNewSchema,modelSchemaDir

if __name__ == '__main__':
    serverAddress='10.97.99.1'
    username='readonly'
    password='juniper@123'

    # serverAddress='10.97.12.3'
    # username='readonly'
    # password='juniper@123'

    serverAddress='10.97.12.2'
    username='readonly'
    password='juniper@123'

    logLevel='info'
    templateDir='./templates/'

    dataRaw,dataNewSchema,modelSchemaDir = asyncio.run(dataCollector(serverAddress,username,password,templateDir,logLevel=logLevel))
    # logging.info(dataRaw)

    # cleaned_data = dataReconstructor(dataRaw, dataNewSchema, modelSchemaDir)

    # logging.info(cleaned_data)
    # logging.info(newData)