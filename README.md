predict.js
==========

Predictive text in JavaScript

This is how I generated the dictionary, index and filter:

python xml2dict.py -d en_us en_us_wordlist.xml

This is the gawk command I sued to generate index.js:

cat en_us.i | gawk -e '{ print("\"" $1 "\": \"" $2 "\","); }' > index.js
