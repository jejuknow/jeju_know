"""Populate the authored Excel shell with the current DB taxonomy, using stdlib only."""
import io
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

from csv_place_import import HEADERS

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
ET.register_namespace('x', NS)


def tag(name):
    return '{' + NS + '}' + name


def column(number):
    text = ''
    while number:
        number, remain = divmod(number - 1, 26)
        text = chr(65 + remain) + text
    return text


def build_workbook(choices):
    path = Path(__file__).parent / 'assets' / 'place-import-template.xlsx'
    output = io.BytesIO()
    with ZipFile(path) as source, ZipFile(output, 'w', ZIP_DEFLATED) as target:
        sheet = ET.fromstring(source.read('xl/worksheets/sheet4.xml'))
        data = sheet.find(tag('sheetData'))
        header_style = data.find(tag('row')).find(tag('c')).get('s', '0')
        data.clear()
        items = list(choices.items())
        for row_number in range(1, max(len(values) for _, values in items) + 2):
            row = ET.SubElement(data, tag('row'), {'r': str(row_number)})
            for index, (title, values) in enumerate(items, 1):
                value = title if row_number == 1 else values[row_number - 2] if row_number - 2 < len(values) else None
                if value is None:
                    continue
                cell = ET.SubElement(row, tag('c'), {'r': column(index) + str(row_number), 't': 'inlineStr', 's': header_style if row_number == 1 else '1'})
                ET.SubElement(ET.SubElement(cell, tag('is')), tag('t')).text = str(value)
        changed = {'xl/worksheets/sheet4.xml': ET.tostring(sheet, encoding='utf-8', xml_declaration=True)}
        book = ET.fromstring(source.read('xl/workbook.xml'))
        names = book.find(tag('definedNames'))
        if names is None:
            names = ET.Element(tag('definedNames'))
            # OOXML definedNames must follow sheets and precede calcPr.
            book.insert(list(book).index(book.find(tag('sheets'))) + 1, names)
        for index, (title, values) in enumerate(items, 1):
            ET.SubElement(names, tag('definedName'), {'name': 'CsvChoice' + str(index)}).text = f"'선택목록'!${column(index)}$2:${column(index)}${max(2,len(values)+1)}"
        changed['xl/workbook.xml'] = ET.tostring(book, encoding='utf-8', xml_declaration=True)
        for sheet_number, kind in enumerate(HEADERS, 1):
            name = f'xl/worksheets/sheet{sheet_number}.xml'
            doc = ET.fromstring(source.read(name))
            validations = doc.find(tag('dataValidations'))
            for rule in validations:
                col = rule.get('sqref').split('2:')[0]
                title = next(h for n,h in enumerate(HEADERS[kind],1) if column(n) == col)
                index = list(choices).index(title) + 1
                rule.find(tag('formula1')).text = 'CsvChoice' + str(index)
                rule.set('allowBlank', '1')
                # Multiple selections are typed with |; validation is authoritative on the server.
                rule.set('showErrorMessage', '0' if title in ('카페_특징','잘맞는동행') else '1')
                rule.set('error', '선택목록에서 현재 값을 확인해주세요.')
                rule.set('errorTitle', '선택값 확인')
            changed[name] = ET.tostring(doc, encoding='utf-8', xml_declaration=True)
        for entry in source.infolist():
            target.writestr(entry.filename, changed.get(entry.filename, source.read(entry.filename)))
    return output.getvalue()
