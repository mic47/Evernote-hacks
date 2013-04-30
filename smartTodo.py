import datetime
import calendar
import re
from xml.etree import ElementTree
import tokens

from evernote.api.client import EvernoteClient
from evernote.edam.type.ttypes import Notebook, Note
import evernote.edam.notestore.ttypes as NodeTypes


dev_token = tokens.developer_token

content_prefix = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">
"""

sections_dict = {'Today:':'today', 'Later:':'later', 'Settings:': 'end',}

def uni(s):
    if s == None:
        return unicode('')
    return unicode(s)

def get_string_from_xml_tree(root):
    return (uni(root.text) + 
            ''.join([get_string_from_xml_tree(child) for child in root]) +
            uni(root.tail))


def contain_node(root, tag):
    if root.tag == tag:
        return True
    for child in root:
        if contain_node(child, tag):
            return True
    return False


def is_new_checklist(root):
    return contain_node(root, 'en-todo')


def is_completed(root):
    if root.tag == 'en-todo':
        if 'checked' not in root.attrib:
            return False
        if root.attrib['checked'] in ['true', True]:
            return True
        return False
    for child in root:
        if is_completed(child):
            return True
    return False
    
    
def get_section(root):
    text = get_string_from_xml_tree(root)
    text = text.strip() 
    if text not in sections_dict:
        return None
    if is_new_checklist(root):
        return None
    if not contain_node(root ,'strong') and not contain_node(root, 'b'):
        return None
    return sections_dict[text]


def split_to_tasks(L):
    out = (list(), list())
    nodes = list()
    completed = False
    for node in L:
        if is_new_checklist(node):
            if len(nodes) > 0:
                out[completed].append(nodes)
            nodes = list()
            completed = is_completed(node)
        nodes.append(node) 
    if len(nodes) > 0:
        out[completed].append(nodes)
    return out
    

def get_tags(root, tag):
    ret = []
    if root.tag == tag:
        return [root]
    for child in root:
        ret.extend(get_tags(child, tag))
    return ret

    
def split_into_sections(root):
    sections = dict()
    sections['start'] = list()
    sections['end'] = list()
    sections['today'] = list()
    sections['later'] = list()
    sections['settings'] = dict()
    sections['completed'] = list()
    #We parse only toplevel node, treat them like one stuff
    section = 'start'
    for child in remove_stupid_divs(split_children_by_line_breaks(root)):
        newsec = get_section(child)
        if newsec != None:
            section = newsec
        if section == 'end':
            for li in get_tags(child, 'li'):
                kv = get_string_from_xml_tree(li).strip().split(':', 1)
                kv = tuple([k.strip() for k in kv])
                if len(kv) != 2: 
                    continue
                key, value = kv
                sections['settings'][key] = value
        sections[section].append(child)
    for sec in ['today', 'later']:
        sections[sec], completed = split_to_tasks(sections[sec])
        sections['completed'].extend(completed)
    return sections


def same_childs_of_this_type(root, tag):
    for child in root:
        if child.tag != tag:
            return False
    return True


def empty_text(a):
    if a == None:
        return True
    if len(a.strip()) == 0:
        return True
    return False


def remove_stupid_divs(lst):
    for part in lst:
        if (part.tag != 'div' or 
            not same_childs_of_this_type(part, 'div') or 
            len(part.attrib) > 0 or
            not empty_text(part.text) or
            not empty_text(part.tail)):
            yield part
        else:
            for child in part:
                for x in remove_stupid_divs(child):
                    yield x


def split_children_by_line_breaks(root):
    for child in root:
        if not contain_node(child, 'br'):
            yield child
            continue
        stack = []
        stack.append(child)
        emit = []
        n = ElementTree.Element(child.tag, child.attrib)
        n.text = child.text
        emit.append(n)
        def boo():
            for ch in stack[-1]:
                if ch.tag == 'br':
                    emit[-1].append(ch)
                    yield emit[0]
                    for i in range(len(emit)):
                        emit[i] = ElementTree.Element(stack[i].tag, stack[i].attrib)
                    for i in range(1, len(emit)):
                        emit[i - 1].append(emit[i])
                    continue
                stack.append(ch)
                n = ElementTree.Element(ch.tag, ch.attrib)
                n.text = ch.text
                emit[-1].append(n)
                emit.append(n)
                for x in boo():
                    yield x
            emit[-1].tail = stack[-1].tail
            if len(emit) == 1:
                yield emit[0]
            emit.pop()
            stack.pop()
        for x in boo():
            yield x
        

def parse_date(date, date_format):
    dt = dict(zip(date_format, map(int,re.findall('\d+', date))))
    if 'd' in dt and 'm' in dt and 'y' in dt:
        return datetime.date(dt['y'], dt['m'], dt['d'])
    return None


def parse_out_due_dates(tasks, default, conversions, date_format):
    out = []
    pattern = re.compile('@due:([^][ \t\n(){}]+)')
    for task in tasks:
        due_date = default
        for node in task:
            text = get_string_from_xml_tree(node)
            srch = pattern.search(text)
            if srch == None:
                continue
            date = srch.group(1)
            if date in conversions:
                due_date = conversions[date]
                continue
            new_date = parse_date(date, date_format)
            if new_date != None:
                due_date = new_date
        out.append((due_date, task))
    return out 
            
def replace_first_string_in_xml(pattern, replacement, root):
    if root.text != None:
        if pattern.search(root.text) != None:
            root.text = pattern.sub(replacement, root.text)
            return True
    for child in root:
        if replace_first_string_in_xml(pattern, replacement, child):
            return True
    if root.tail != None:
        if pattern.search(root.tail) != None:
            root.tail = pattern.sub(replacement, root.tail)
            return True
    return False
            
def update_tasks(tasks, date_format, separator):
    pattern = re.compile('@due:([^][ \t\n(){}]+)')
    output = list()
    for date, task in tasks:
        dt = {'d': date.day, 'm': date.month, 'y': date.year}
        replacement = '@due:{date}'.format(
            date=separator.join([str(dt[x]) for x in date_format])
        )
        replaced = False
        for line in task:
            if replace_first_string_in_xml(pattern, replacement, line):
                replaced = True
                break
        if not replaced and get_section(task[0]) == None:
            if len(task[0]) > 0:
                if task[0][-1].tail == None:
                    task[0][-1].tail = ''
                task[0][-1].tail += ' ' + replacement + ' '
            else:
                if task[0].text == None:
                    task[0].text = ''
                    
                task[0].text += ' ' + replacement + ' '
        output.append(task)
    return output


def date_to_string(date, date_format, separator):
    dt = {'d': date.day, 'm': date.month, 'y': date.year}
    return separator.join([str(dt[x]) for x in date_format])


def get_history_note_title(prefix, date, tp, date_format, separator):
    if tp not in ['daily', 'weekly', 'monthly']:
        tp = 'weekly'
    if tp == 'daily':
        return '{} ({})'.format(prefix, date_to_string(date, 
                                                       date_format, 
                                                       separator))
    if tp == 'weekly':
        monday = date - datetime.timedelta(date.weekday())
        sunday = date + datetime.timedelta(6 - date.weekday())
        return '{} ({} - {})'.format(
                prefix,
                date_to_string(monday, date_format, separator),
                date_to_string(sunday, date_format, separator)
        )
    if tp == 'monthly':
        first_day = date - datetime.timedelta(date.day - 1)
        last_day = (date + 
                    datetime.timedelta(calendar.monthrange(date.year, 
                                                           date.month)[1] - 
                                       date.day))
        return '{} ({} - {})'.format(
            prefix,
            date_to_string(first_day, date_format, separator),
            date_to_string(last_day, date_format, separator)
        )
    return None


client = EvernoteClient(token=dev_token, sandbox=False)
noteStore = client.get_note_store()
noteStore = client.get_note_store()
Filter=NodeTypes.NoteFilter()
Filter.words = 'tag:@smarttodo'
notes = noteStore.findNotes(dev_token, Filter, 0, 10)
for note in notes.notes:
    nt = noteStore.getNote(dev_token, note.guid, True, False, False, False)
    root = ElementTree.fromstring(nt.content)
    ElementTree.dump(root)
    sections = split_into_sections(root)
    today = datetime.date.today() - datetime.timedelta(1)
    tomorrow = today + datetime.timedelta(1)
    conversions = {
        'today': today,
        'tomorrow': tomorrow,
        'yesterday': today - datetime.timedelta(1),
    }
    print sections
    unfinished = parse_out_due_dates(sections['today'][1:], today, conversions,
                                     sections['settings']['Date format'])
    unfinished.extend(
        parse_out_due_dates(sections['later'][1:], tomorrow, conversions,
                            sections['settings']['Date format']))
    new_today_list = [x for x in unfinished if x[0] <= tomorrow]
    new_tomorrow_list = [x for x in unfinished if x[0] > tomorrow]
    new_tomorrow_list.sort(key=lambda x: x[0])
    sections['today'][1:] = update_tasks(new_today_list, sections['settings']['Date format'], sections['settings']['Date separator'])
    sections['later'][1:] = update_tasks(new_tomorrow_list, sections['settings']['Date format'], sections['settings']['Date separator'])
    text, tail, attrib, tag = root.text, root.tail, root.attrib, root.tag
    root.clear()
    root.text, root.tail, root.attrib, root.tag = text, tail, attrib, tag
    for sec in ['start', 'today', 'later', 'end']:
        for section in sections[sec]:
            if sec in ['today', 'later']:
                root.extend(section)
            else:
                root.append(section)
    new_node_content = ElementTree.tostring(root, 'utf-8')
    nt.content = content_prefix + new_node_content
    print 'Updated:'
    ElementTree.dump(root)
    noteStore.updateNote(dev_token, nt)
    
    history_notebook = sections['settings']['History notebook'].strip()
    history_interval = sections['settings']['History interval'].strip()
    history_prefix = sections['settings']['History note'].strip()    
    history_title = get_history_note_title(history_prefix, today, 
                                           history_interval, 
                                           sections['settings']['Date format'], 
                                           sections['settings']['Date separator'])
    notebooks = noteStore.listNotebooks(dev_token)
    notebook_guid = None
    for notebook in notebooks:
        if notebook.name == history_notebook:
            notebook_guid = notebook.guid
    if notebook_guid == None:
        notebook = Notebook()
        notebook.name = history_notebook
        notebook = noteStore.createNotebook(dev_token, notebook)
        notebook_guid = notebook.guid
    Filter = NodeTypes.NoteFilter()
    Filter.notebookGuid = notebook_guid
    Filter.words = 'intitle:' + history_title
    history_notes = noteStore.findNotes(dev_token, Filter, 0, 1)
    if len(history_notes.notes) < 1:
        hist_root = ElementTree.Element('en-note')
        hist_note = Note()
        hist_note.title = history_title
        hist_note.notebookGuid = notebook_guid
    else:
        hist_note = noteStore.getNote(dev_token, history_notes.notes[0].guid, 
                                      True, False, False, False)
        hist_root = ElementTree.fromstring(hist_note.content)
    day_element = ElementTree.fromstring('<div><strong>{}</strong></div>'.format(
        date_to_string(today,
                       sections['settings']['Date format'], 
                       sections['settings']['Date separator'])))
    hist_root.append(day_element)
    for x in sections['completed']:
        hist_root.extend(x)
    hist_note.content = content_prefix + ElementTree.tostring(hist_root, 'utf-8')
    if len(history_notes.notes) < 1:
        noteStore.createNote(dev_token, hist_note)
    else:
        noteStore.updateNote(dev_token, hist_note)
    #TODO: Budu sa mi tu kotit prazdne divy 
    #TODO: ak nastane nejaka chyba, updatni to a na konci povedz ze bola chyba
