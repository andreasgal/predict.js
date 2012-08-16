from array import array
from optparse import OptionParser 
from xml.dom.minidom import parseString
from io import BytesIO
from StringIO import StringIO
from collections import defaultdict
import sys, struct, operator, heapq

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

# the alphabet with frequency counts
symbol_freq = defaultdict(int)
EndOfWord = '*'
EndOfPrefix = '#'

# the vocabulary
vocabulary = []

# prefix index
index = {}

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
    # count the symbol frequency
    for ch in word:
        symbol_freq[ch] += 1
    symbol_freq[EndOfWord] += 1
    # add to the vocabulary
    vocabulary.append([word, freq, flags])
    # add prefixes to the index
    prefix = word[0:min(len(word), 6)]
    setbit(prefix)
    short = word[len(prefix):]
    if not prefix in index:
        index[prefix] = {}
        symbol_freq[EndOfPrefix] += 1
    if short in index[prefix]:
      index[prefix][short] += freq # combines entries if we processed word into something simpler
    else:
      index[prefix][short] = freq

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

# build a huffman code table for the given symbol/frequency dictionary
def buildHuffmanTable(symbols):
    sorted_symbols = sorted(symbols.iteritems(), key=operator.itemgetter(1))
    trees = list()
    for symbol, freq in sorted_symbols:
        trees.append((freq, symbol))
    heapq.heapify(trees)
    while len(trees) > 1:
        childR, childL = heapq.heappop(trees), heapq.heappop(trees)
        parent = (childL[0] + childR[0], childL, childR)
        heapq.heappush(trees, parent)

    codes = {}
    def buildCodeTable(tree, prefix = ''):
        if len(tree) == 2:
            codes[tree[1]] = prefix
        else:
            buildCodeTable(tree[1], prefix + '0')
            buildCodeTable(tree[2], prefix + '1')
    buildCodeTable(trees[0])
    return codes

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
    output.write('"' + key + '": "' + ':'.join([short + '/' + str(word[short]) for short in word]) + '",\n')
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

# Create a trie that we will use to look up prefixes
def buildTrie():
    root = {}
    for prefix, suffixes in index.iteritems():
        node = root
        while prefix != "":
            ch = prefix[0:1]
            prefix = prefix[1:]
            if not ch in node:
                node[ch] = {}
            node = node[ch]
        node["data"] = suffixes
    return root

# Create the huffman code table we will use to compress words
codes = buildHuffmanTable(symbol_freq)

bitstring = StringIO()
def encodeString(output, s):
    for ch in s:
        output.write(codes[ch])
    output.write(codes[EndOfWord])
def asBitString(i):
    return bin(i).lstrip('0b')
def encodeByte(output, b):
    output.write(asBitString(b).zfill(8))
def encodeRawShort(output, s):
    encodeByte(output, (s >> 8) & 0xff)
    encodeByte(output, s & 0xff)
def encodeShort(output, s):
    if s > 255:
        encodeByte(output, 255)
        encodeRawShort(output, s)
    else:
        encodeByte(output, s)
def encodeOffset(output, offset):
    encodeShort(output, offset)
def encodeChar(output, ch):
    encodeShort(output, ord(ch))
def flushByte(output):
    while not output.tell() % 8 == 0:
        output.write("0")

# Emit the huffman table
def emitHuffmanTable(output, codes):
    encodeShort(output, len(codes))
    for ch, code in codes.iteritems():
        encodeShort(output, ord(ch))
        encodeByte(output, len(code))
    for ch, code in codes.iteritems():
        output.write(code)

# Emit the trie, compressing the symbol index
def emitTrie(output, trie):
    fixup = 0
    s = ""
    # All offsets are relative to the beginning of the prefix/offset table.
    start = output.tell() / 8
    # Emit the prefixes and the offset we recorded the last time we called
    # emitTrie. We will verify later that the offset is accurate and emit
    # the tree again in case offsets shifted around.
    for ch in trie:
        if not len(ch) == 1:
            continue
        child = trie[ch]
        offset = 0
        if "offset" in child:
            offset = child["offset"]
        encodeChar(output, ch)
        encodeOffset(output, offset)
    if "data" in trie:
        # Emit the list of prefixes, compressed using the Huffman codes.
        suffixes = trie["data"]
        for suffix, freq in suffixes.iteritems():
            encodeString(output, suffix)
            encodeByte(output, freq)
    # Mark the end of the prefixes.
    encodeString(output, EndOfPrefix)
    flushByte(output)
    # Emit the child nodes of this node.
    for ch in trie:
        # Ignore meta nodes like offset and data.
        if len(ch) != 1:
            continue
        child = trie[ch]
        # Count the number of fixups we did.
        here = output.tell() / 8
        offset = here - start
        if not "offset" in child or child["offset"] != offset:
            fixup += 1
            child["offset"] = offset
        # Track whether any of our children requires emitting the file again.
        fixup += emitTrie(output, child)
    return fixup

trie = buildTrie()
# Emit the trie until the offsets stabilize.
while True:
    bitstring = StringIO()
    emitHuffmanTable(bitstring, codes)
    if emitTrie(bitstring, trie) == 0:
        break

# Write the compressed index to disk.
output = BytesIO()
bitstring.seek(0)
while bitstring.tell() < bitstring.len:
    output.write(struct.pack("B", int(bitstring.read(8), 2)))
print("compressed index size: {0} bytes".format(output.tell()))
output.seek(0)
f = open(options.dict + ".dict", "w")
f.write(output.read())
f.close()
