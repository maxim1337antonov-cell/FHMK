with open("test_withdraw.py", "r", encoding="utf-8") as f:
    text = f.read()

# pytest-asyncio needs tests to be marked with @pytest.mark.asyncio
text = text.replace("async def test():", "@pytest.mark.asyncio\nasync def test():")
text = "import pytest\n" + text

if text.count('"""') % 2 != 0:
    text += '\n"""\n'

with open("test_withdraw.py", "w", encoding="utf-8") as f:
    f.write(text)
