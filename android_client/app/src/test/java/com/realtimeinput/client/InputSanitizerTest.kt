package com.realtimeinput.client

import org.junit.Assert.assertEquals
import org.junit.Test

class InputSanitizerTest {
    @Test
    fun longDictationEndingWithNewlinePreservesAllText() {
        val dictated = "这是一段已经梳理好的长文字，前面的所有内容都必须保留，不能因为输入法最后提交了换行就整段回滚。"
        assertEquals("$dictated ", InputSanitizer.normalizeControlChars("$dictated\n"))
    }

    @Test
    fun controlCharactersAreNormalizedWithoutDroppingSurroundingText() {
        assertEquals("甲 乙 丙 丁 戊", InputSanitizer.normalizeControlChars("甲\r\n乙\t丙\r丁\b戊"))
    }

    @Test
    fun ordinaryTextIsUnchanged() {
        val text = "普通中文、English 123，保持原样。"
        assertEquals(text, InputSanitizer.normalizeControlChars(text))
    }
}
