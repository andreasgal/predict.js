var dict = snarf("en_us.dict", "binary");

const PrefixLimit = dict[0];
const BloomFilterSize = dict[1] * 65536;

var Filter = (function () {
    var mask = BloomFilterSize - 1;
    return (function (hash) {
        var offset = hash >> 3;
        var bit = hash & 7;
        return !!(dict[2 + (offset & mask)] & (1 << bit));
    });
})();

var LookupPrefix = (function () {
    // Markers used to terminate prefix/offset tables.
    const EndOfPrefixesSuffixesFollow = '#';
    const EndOfPrefixesNoSuffixes = '&';

    // Skip over the header bytes and the bloom filter data.
    var pos = 2 + BloomFilterSize;

    // Read an unsigned byte.
    function getByte() {
	return dict[pos++];
    }

    // Read a variable length unsigned integer.
    function getVLU() {
	var u = 0
	var shift = 0;
	do {
	    var b = dict[pos++];
	    u |= (b & 0x7f) << shift;
	    shift += 7;
	} while (b & 0x80);
	return u;
    }

    // Read a 0-terminated string.
    function getString() {
	var s = "";
	var u;
	while ((u = getVLU()) != 0)
	    s += String.fromCharCode(u);
	return s;
    }

    // Return the current position.
    function tell() {
	return pos;
    }

    // Seek to a byte position in the stream.
    function seekTo(newpos) {
	pos = newpos;
    }

    // Remember the start position of the trie.
    var init = pos;

    // Skip over the prefix/offset pairs and find the list of suffixes and add
    // them to the result set.
    function AddSuffixes(prefix, result) {
	while (true) {
	    var ch = String.fromCharCode(getVLU());
	    if (ch == EndOfPrefixesNoSuffixes)
		return result; // No suffixes, done.
	    if (ch == EndOfPrefixesSuffixesFollow) {
		var freq;
		while ((freq = getByte()) != 0) {
		    var word = prefix + getString();
		    result.push({word: word, freq: freq});
		}
		return; // Done.
	    }
	    getVLU(); // ignore offset
	}
    }

    // Search matching trie branches at the current position (pos) for the next
    // character in the prefix. Keep track of the actual prefix path taken
    // in path, since we collapse certain characters in to bloom filter
    // (e.g. upper case/lower case). If found, follow the next prefix character
    // if we have not reached the end of the prefix yet, otherwise add the
    // suffixes to the result set.
    function SearchPrefix(prefix, path, result) {
	var p = prefix[path.length].toLowerCase();
	var last = 0;
	while (true) {
	    var ch = String.fromCharCode(getVLU());
	    if (ch == EndOfPrefixesNoSuffixes ||
		ch == EndOfPrefixesSuffixesFollow) {
		// No matching branch in the trie, done.
		return;
	    }
	    var offset = getVLU() + last;
	    if (ch.toLowerCase() == p) { // Matching prefix, follow the branch in the trie.
		var saved = tell();
		seekTo(offset);
		var path2 = path + ch;
		if (path2.length == prefix.length)
		    AddSuffixes(path2, result);
		else
		    SearchPrefix(prefix, path2, result);
		seekTo(saved);
	    }
	    last = offset;
	}
    }

    return (function (prefix) {
        var result = [];

	// Rewind to the start of the trie.
	pos = init;

	SearchPrefix(prefix, "", result);

	return result;
    });
})();

// Turn a character into a key: turn upper case into lower case
// and convert all umlauts into the base character.
const ToKey = (function () {
    const mapping = {
        "é": "e"
    };
    return (function (ch) {
        ch = ch.toLowerCase();
        var ch2 = mapping[ch];
        return (ch2 ? ch2 : ch).charCodeAt(0);
    });
})();

// Return all possible characters a key could mean.
const NearbyKeys = {
    q: "was",
    w: "qasde",
    e: "wsdfr",
    r: "edfgt",
    t: "rfghy",
    y: "tghju",
    u: "yhjki",
    i: "ujklo",
    o: "ikl'p",
    p: "ol'",
    a: "qws",
    s: "aqwedz",
    d: "swerfxz",
    f: "dertgcx",
    g: "frtyhvc",
    h: "gtyujbv",
    j: "hyuiknb",
    k: "juiolmn",
    l: "kiop'm",
    "'": "lop",
    z: "sdx",
    x: "zdfc",
    c: "xfgv",
    v: "cghb",
    b: "vhjn",
    n: "bjkm",
    m: "nkl"
};

var AllKeys = (function () {
    var s = "";
    for (i in NearbyKeys)
        s += i;
    return s;
})();

// Generate an array of char codes from a word.
function String2Codes(word) {
    var codes = new Uint8Array(word.length);
    for (var n = 0; n < codes.length; ++n)
        codes[n] = ToKey(word[n]);
    return codes;
}

// Convert an array of char codes back into a string.
function Codes2String(codes) {
    var s = "";
    for (var n = 0; n < codes.length; ++n)
        s += String.fromCharCode(codes[n]);
    return s;
}

function Check(input, candidates) {
    var h1 = 0;
    var h2 = 0xdeadbeef;
    for (var n = 0; n < input.length; ++n) {
	var ch = input[n];
        h1 = h1 * 33 + ch;
        h1 = h1 & 0xffffffff;
        h2 = h2 * 73 ^ ch;
	h2 = h2 & 0xffffffff;
    }
    if (Filter(h1) && Filter(h2)) {
        var prefix = Codes2String(input);
        var result = LookupPrefix(prefix);
        if (result) {
            for (var n = 0; n < result.length; ++n)
                candidates.push(result[n]);
        }
    }
}

// Generate all candidates with an edit distance of 1.
function EditDistance1(input, candidates) {
    var length = input.length;
    for (var n = 0; n < length; ++n) {
        var key = input[n];
        var nearby = NearbyKeys[String.fromCharCode(key)];
        for (var i = 0; i < nearby.length; ++i) {
            input[n] = nearby[i].charCodeAt(0);
            Check(input, candidates);
        }
        input[n] = key;
    }
}

// Generate all candidates with an edit distance of 2.
function EditDistance2(input, candidates) {
    var length = input.length;
    if (length < 4)
        return;
    for (var n = 0; n < length; ++n) {
        for (var m = 1; m < length; ++m) {
            if (n == m)
                continue;
            var key1 = input[n];
            var key2 = input[m];
            var nearby1 = NearbyKeys[String.fromCharCode(key1)];
            var nearby2 = NearbyKeys[String.fromCharCode(key2)];
            for (var i = 0; i < nearby1.length; ++i) {
                for (var j = 0; j < nearby2.length; ++j) {
                    input[n] = nearby1[i].charCodeAt(0);
                    input[m] = nearby2[j].charCodeAt(0);
                    Check(input, candidates);
                }
            }
            input[n] = key1;
            input[m] = key2;
        }
    }
}

// Generate all candidates with a missing character.
function Omission1Candidates(input, candidates) {
    var length = Math.min(input.length, PrefixLimit - 1);
    var input2 = Uint8Array(length + 1);
    for (var n = 1; n <= length; ++n) {
        for (var i = 0; i < n; ++i)
            input2[i] = input[i];
        while (i < length)
            input2[i+1] = input[i++];
        for (i = 0; i < AllKeys.length; ++i) {
            input2[n] = AllKeys[i].charCodeAt(0);
            Check(input2, candidates);
        }
    }
}

// Generate all candidates with a single extra character.
function Deletion1Candidates(input, candidates) {
    var length = input.length;
    var input2 = Uint8Array(length - 1);
    for (var n = 1; n < length; ++n) {
        for (var i = 0; i < n; ++i)
            input2[i] = input[i];
        ++i;
        while (i < length)
            input2[i-1] = input[i++];
        Check(input2, candidates);
    }
}

var LevenshteinDistance = (function () {
    var matrix = [];

    return function(a, b) {
        if (a.length == 0) return b.length;
        if (b.length == 0) return a.length;

        // increment along the first column of each row
        for (var i = 0; i <= b.length; i++)
            matrix[i] = [i];

        // increment each column in the first row
        for (var j = 0; j <= a.length; j++)
            matrix[0][j] = j;

        // Fill in the rest of the matrix
        for (i = 1; i <= b.length; i++){
            for (j = 1; j <= a.length; j++){
                if (b.charAt(i-1) == a.charAt(j-1)) {
                    matrix[i][j] = matrix[i-1][j-1];
                } else {
                    matrix[i][j] = Math.min(matrix[i-1][j-1] + 1, // substitution
                                            Math.min(matrix[i][j-1] + 1, // insertion
                                                     matrix[i-1][j] + 1)); // deletion
                }
            }
        }

        return matrix[b.length][a.length];
    };
})();

function AutoCorrect(word) {
    // This is the list where we will collect all the candidate words.
    var candidates = [];
    // Limit search by prefix to avoid long lookup times.
    var prefix = word.substr(0, PrefixLimit);
    // Check for the current input, edit distance 1 and 2 and single letter
    // omission and deletion in the prefix.
    var input = String2Codes(prefix);
    Check(input, candidates);
    EditDistance1(input, candidates);
    EditDistance2(input, candidates);
    Omission1Candidates(input, candidates);
    Deletion1Candidates(input, candidates);
    // Now find the candidate with the least Levenshtein distance to the
    // actual input.
    var minimum = Infinity;
    var frequency = 0;
    var result = "";
    for (var n = 0; n < candidates.length; ++n) {
        var candidate = candidates[n];
        var candidate_word = candidate.word;
        var candidate_freq = candidate.freq;
        // Calculate the distance of the word that was entered so far to the
        // same number of letters from the candidate.
        distance = LevenshteinDistance(word, candidate_word.substr(0, word.length));
        if (distance <= minimum && (distance < minimum || candidate_freq > frequency)) {
            minimum = distance;
            frequency = candidate_freq;
            result = candidate_word;
        }
    }
    return result;
}

var t = Date.now();
for (var n = 0; n < 100; ++n)
    var result = AutoCorrect(arguments[0] || "door");
print((Date.now() - t) / 100 + " ms");
print(result);
