package com.realtimeinput.client

object InputSanitizer {
    fun normalizeControlChars(text: String): String {
        return text
            .replace("\r\n", " ")
            .replace('\n', ' ')
            .replace('\r', ' ')
            .replace('\t', ' ')
            .replace('\b', ' ')
    }
}
