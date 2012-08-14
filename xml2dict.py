from array import array
from optparse import OptionParser 
from xml.dom.minidom import parseString
from io import BytesIO
from StringIO import StringIO
import sys, struct, operator

# parse command line arguments
use = "Usage: %prog [options] dictionary.xml"
parser = OptionParser(usage = use)
parser.add_option("-v", "--verbose", dest="verbose", action="store_true", default=False, help="Set mode to verbose.")
parser.add_option("-d", "--output-dictionary", dest="dict", metavar="FILE", help="write dictionary output to FILE")
options, args = parser.parse_args()

# we expect the dictionary name to be present
if len(args) < 1:
    print("Missing dictionary name.")
    exit(-1)
if options.dict == None:
    print("Missing output file.")
    exit(-1)

# read the input dictionary file
file = open(args[0])
data = file.read()
file.close()

# the vocabulary
vocabulary = []

# prefix index
index = dict([])

# the in-memory bloom filter
BloomFilterSize = 512*1024
bf = array('B')
for i in range(BloomFilterSize):
    bf.append(0)

def hash(word):
    h = 0xcc9e2d51
    for i in range(len(word)):
        k = ord(word[i])
        h = ((h<<5)-h)+k
        h = h & h # convert to 32bit integer
    return h

def setbit(word):
    h = hash(word)
    bf[(h / 8) % BloomFilterSize] |= (1 << (h % 8))

def hasbit(word):
    h = hash(word)
    return (bf[(h / 8) % BloomFilterSize] & (1 << (h % 8))) != 0

def add(word, freq, flags):
    if freq <= 1: return # ignore extremely infrequent words
    # Remove trailing 's
    if word.endswith("'s"):
        word = word[:-2]
    # add to the vocabulary
    vocabulary.append([word, freq, flags])
    # add prefixes to the index
    prefix = word[0:min(len(word), 6)]
    setbit(prefix)
    short = word[len(prefix):]
    if not prefix in index:
        index[prefix] = short + "/" + str(freq)
    else:
        index[prefix] = index[prefix] + ":" + short + "/" + str(freq)

# go through the dictionary and build the trie
dom = parseString(data)
wordlist = dom.getElementsByTagName("wordlist")[0]
words = wordlist.getElementsByTagName("w")
for word in words:
    attr = word.attributes
    flags = attr.get("flags")
    if flags != None:
        flags = flags.nodeValue
    else:
        flags = ""
    freq = int(attr.get("f").nodeValue)
    text = word.childNodes[0].nodeValue
    add(text, freq, flags)

# Do some statistical sanity checking:
print("index entries: {0}".format(len(index)))

# Write the vocabulary to disk.
output = StringIO()
for word, freq, flags in vocabulary:
    output.write(word + " " + str(freq) + " " + flags + "\n")
print("vocabulary size: {0} words, {1} bytes".format(len(vocabulary), output.tell()))
output.seek(0)
f = open(options.dict + ".dict", "w")
f.write(output.read().encode("utf-8"))
f.close()

# Write the index to disk
output = StringIO()
output.write('{\n')
for key, word in index.iteritems():
    output.write('"' + key + '": "' + word + '",\n')
output.write('}\n')
print("index size: {0} words, {1} bytes".format(len(index), output.tell()))
output.seek(0)
f = open(options.dict + ".i", "w")
f.write(output.read().encode("utf-8"))
f.close()

# Write the bloom filter for the index
output = BytesIO()
for b in bf:
    output.write(struct.pack("B", b))
output.seek(0)
f = open(options.dict + ".bf", "w")
f.write(output.read())
f.close()
