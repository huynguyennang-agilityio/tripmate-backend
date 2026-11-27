// Throwaway test for F15's Node 24 probe: it fails because ./missing.js does not exist yet.
const test = require('node:test');
const { add } = require('./missing.js');
test('adds', () => { if (add(1, 2) !== 3) throw new Error('wrong'); });
