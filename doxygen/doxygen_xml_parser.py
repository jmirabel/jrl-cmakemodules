#!/usr/bin/python3

from __future__ import print_function
from lxml import etree
from os import path
from xml_docstring import XmlDocString
import sys

template_file_header = \
"""#ifndef DOXYGEN_AUTODOC_HH
#define DOXYGEN_AUTODOC_HH

#include "{path}/doxygen.hh"

namespace doxygen {{
"""
template_file_footer = \
"""
} // namespace doxygen
#endif // DOXYGEN_AUTODOC_HH
"""

template_constructor_doc = \
"""
template <{tplargs}>
struct constructor_doc_{nargs}_impl< {classname_prefix}{comma}{argsstring} >
{{
static inline const char* run ()
{{
  return "{docstring}";
}}
}};"""
template_destructor_doc = \
"""
template <{tplargs}>
struct destructor_doc_impl < {classname_prefix} >
{{
static inline const char* run ()
{{
  return "{docstring}";
}}
}};"""
template_member_func_doc = \
"""
{template}inline const char* member_func_doc ({rettype} ({classname_prefix}*function_ptr) {argsstring})
{{{body}
  return "";
}}"""
template_member_func_doc_body = \
"""
  if (function_ptr == static_cast<{rettype} ({classname_prefix}*) {argsstring}>(&{classname_prefix}{membername}))
    return "{docstring}";"""
template_open_namespace = \
"""namespace {namespace} {{"""
template_close_namespace = \
"""}} // namespace {namespace}"""

def _templateParamToDict (param):
    type = param.find('type')
    declname = param.find('declname')
    defname  = param.find('defname')
    # FIXME type may contain references in two ways:
    # - the real param type
    # - the name of the template argument is recognized as the name of a type...
    if defname is None and declname is None:
        typetext = type.text
        for c in type.iter():
            if c == type: continue
            if c.text is not None: typetext += c.text
            if c.tail is not None: typetext += c.tail
        if typetext.startswith ("typename") or typetext.startswith ("class"):
            s = typetext.split(maxsplit=1)
            assert len(s) == 2
            return { "type": s[0].strip(), "name": s[1].strip() }
        else:
            return { "type": type.text, "name": "" }
    else:
        assert defname.text == declname.text
        return { "type": type.text, "name": defname.text }

def format_description (brief, detailed):
    b = [ el.text.strip() for el in brief   .iter() if el.text ] if brief    is not None else []
    d = [ el.text.strip() for el in detailed.iter() if el.text ] if detailed is not None else []
    text = "".join(b)
    if d:
        text += '\n' + "".join(d)
    return text

class Reference(object):
    def __init__ (self, index, id=None, name=None):
        self.id = id
        self.name = name
        self.index = index

    def xmlToType (self, node, parentClass=None, tplargs=None):
        """
        - node:
        - parentClass: a class
        - tplargs: if one of the args is parentClass and no template arguments are provided,
                   set the template arguments to this value
        """
        if node.text is not None:
            t = node.text.strip()
        else:
            t = ""
        for c in node.iterchildren():
            if c.tag == "ref":
                refid = c.attrib["refid"]
                if parentClass is not None and refid == parentClass.id:
                    t += " " + parentClass.name
                    if c.tail is not None and c.tail.lstrip()[0] != '<':
                        t += tplargs
                elif self.index.hasref(refid):
                    t += " " + self.index.getref(refid).name
                else:
                    self.index.output.err ("Unknown reference: ", c.text, refid)
                    t += " " + c.text.strip()
            else:
                if c.text is not None:
                    t += " " + c.text.strip()
            if c.tail is not None:
                t += " " + c.tail.strip()
        return t

# Only for function as of now.
class MemberDef(Reference):
    def __init__ (self, index, memberdefxml, parent):
        super().__init__ (index=index,
                id = memberdefxml.attrib["id"],
                name = memberdefxml.find("definition").text)
        self.parent = parent

        self.xml = memberdefxml
        self.const = (memberdefxml.attrib['const']=="yes")
        self.static = (memberdefxml.attrib['static']=="yes")
        self.rettype = memberdefxml.find('type')
        self.params = tuple( [ param.find('type') for param in self.xml.findall("param") ] )
        self.special = self.rettype.text is None and len(self.rettype.getchildren())==0
        #assert self.special or len(self.rettype.text) > 0

        self._templateParams (self.xml.find('templateparamlist'))

    def _templateParams (self, tpl):
        if tpl is not None:
            self.template_params = tuple ([ _templateParamToDict(param) for param in tpl.iterchildren(tag="param") ])
        else:
            self.template_params = tuple()

    def prototypekey (self):
        prototype = (
                self.xmlToType(self.rettype),
                tuple( [ tuple(t.items()) for t in self.template_params ]),
                tuple( [ self.xmlToType(param.find('type')) for param in self.xml.findall("param") ] ),
                self.const,
                )
        return prototype

    def s_prototypeArgs (self):
        return "({0}){1}".format (self.s_args(), " const" if self.const else "")

    def s_args (self):
        # If the class is templated, check if one of the argument is the class itself.
        # If so, we must add the template arguments to the class (if there is none)

        if len(self.parent.template_params) > 0:
            tplargs = " <" + ", ".join([ d['name'] for d in self.parent.template_params ]) + " > "
            args = ", ".join(
                    [ self.xmlToType(t, parentClass=self.parent, tplargs=tplargs) for t in self.params])
        else:
            args = ", ".join([ self.xmlToType(t) for t in self.params])
        return args

    def s_tpldecl (self):
        if len(self.template_params) == 0: return ""
        return ", ".join([ d['type'] + " " + d['name'] for d in self.template_params ])

    def s_rettype (self):
        assert not self.special
        return self.xmlToType(self.rettype)

    def s_name (self):
        return self.xml.find('name').text.strip()

    def s_docstring (self):
        return self.index.xml_docstring.getDocString (
                self.xml.find('briefdescription'),
                self.xml.find('detaileddescription'),
                self.index.output)

class CompoundBase(Reference):
    def __init__ (self, compound, index):
        self.compound = compound
        self.filename = path.join (index.directory, compound.attrib["refid"]+".xml")
        self.tree = etree.parse (self.filename)
        self.definition = self.tree.getroot().find("compounddef")
        super().__init__ (index,
                id = self.definition.attrib['id'],
                name = self.definition.find("compoundname").text)

class NamespaceCompound (CompoundBase):
    def __init__ (self, *args):
        super().__init__ (*args)
        self.typedefs = []
        self.enums = []

        # Add references
        for section in self.definition.iterchildren("sectiondef"):
            assert "kind" in section.attrib
            if section.attrib["kind"] == "enum":
                self.parseEnumSection (section)
            elif section.attrib["kind"] == "typedef":
                self.parseTypedefSection (section)

    def parseEnumSection (self, section):
        for member in section.iterchildren("memberdef"):
            ref = Reference (index=self.index,
                    id=member.attrib["id"],
                    name= self.name + "::" + member.find("name").text)
            self.index.registerReference (ref)
            self.enums.append(member)
            for value in member.iterchildren("enumvalue"):
                ref = Reference (index=self.index,
                        id=value.attrib["id"],
                        name= self.name + "::" + member.find("name").text)

    def parseTypedefSection (self, section):
        for member in section.iterchildren("memberdef"):
            ref = Reference (index=self.index,
                    id=member.attrib["id"],
                    name= self.name + "::" + member.find("name").text)
            self.index.registerReference (ref)
            self.typedefs.append(member)

    def write (self, output):
        pass

class ClassCompound (CompoundBase):
    def __init__ (self, *args):
        super().__init__ (*args)
        self.member_funcs = list()
        self.static_funcs = list()
        self.special_funcs = list()

        self.struct = (self.compound.attrib['kind'] == "struct")
        self.public = (self.definition.attrib['prot'] == "public")
        self.template_specialization = (self.name.find('<') > 0)

        # Handle templates
        self._templateParams (self.definition.find('templateparamlist'))
        for memberdef in self.definition.iter(tag="memberdef"):
            if memberdef.attrib['prot'] != "public":
                continue
            if memberdef.attrib['kind'] == "variable":
                self._attribute (memberdef)
            elif memberdef.attrib['kind'] == "typedef":
                ref = Reference (index=self.index,
                        id=memberdef.attrib["id"],
                        name= self.name + "::" + memberdef.find("name").text)
                self.index.registerReference (ref)
            elif memberdef.attrib['kind'] == "enum":
                ref = Reference (index=self.index,
                        id=memberdef.attrib["id"],
                        name= self.name + "::" + memberdef.find("name").text)
                self.index.registerReference (ref)
                for value in memberdef.iterchildren("enumvalue"):
                    ref = Reference (index=self.index,
                            id=value.attrib["id"],
                            name= self.name + "::" + memberdef.find("name").text)
                    self.index.registerReference (ref)
            elif memberdef.attrib['kind'] == "function":
                self._memberfunc (memberdef)

    def _templateParams (self, tpl):
        if tpl is not None:
            self.template_params = tuple([ _templateParamToDict(param) for param in tpl.iterchildren(tag="param") ])
        else:
            self.template_params = tuple()

    def _templateDecl (self):
        if not hasattr(self, "template_params") or len(self.template_params) == 0:
            return ""
        return ", ".join([ d['type'] + " " + d['name'] for d in self.template_params ])

    def _className (self):
        if not hasattr(self, "template_params") or len(self.template_params) == 0:
            return self.name
        return self.name + " <" + ", ".join([ d['name'] for d in self.template_params ]) + " >"

    def _memberfunc (self, member):
        m = MemberDef (self.index, member, self)
        if m.special:
            self.special_funcs.append (m)
        elif m.static:
            self.static_funcs.append (m)
        else:
            self.member_funcs.append (m)

    def write (self, output):
        if not self.public: return
        if self.template_specialization:
            output.err ("Disable class {} because template argument are not resolved for templated class specialization.".format(self.name))
            return
        # Group member function by prototype
        member_funcs = dict()
        for m in self.member_funcs:
            prototype = m.prototypekey()
            if prototype in member_funcs:
                member_funcs[prototype].append (m)
            else:
                member_funcs[prototype] = [ m, ]

        classname_prefix = self._className() + "::"

        for member in self.special_funcs:
            docstring = member.s_docstring()
            if len(docstring) == 0: continue
            if member.s_name()[0] == '~':
                output.out (template_destructor_doc.format (
                    tplargs = self._templateDecl(),
                    classname_prefix = self._className(),
                    docstring = docstring,
                    ))
            else:
                output.out (template_constructor_doc.format (
                    tplargs = ", ".join([ d['type'] + " " + d['name'] for d in self.template_params + member.template_params ]),
                    nargs = len(member.params),
                    comma = ", " if len(member.params) > 0 else "",
                    classname_prefix = self._className(),
                    argsstring = member.s_args(),
                    docstring = docstring,
                    ))

        for prototype, members in member_funcs.items():
            # remove undocumented members
            documented_members = []
            docstrings = []
            for member in members:
                docstring = member.s_docstring()
                if len(docstring) == 0: continue
                documented_members.append (member)
                docstrings.append (docstring)
            if len(documented_members) == 0: continue

            body = "".join([
                template_member_func_doc_body.format (
                    classname_prefix = classname_prefix,
                    membername = member.s_name(),
                    docstring = docstring,
                    rettype = member.s_rettype(),
                    argsstring = member.s_prototypeArgs(),
                    )
                for member, docstring in zip(documented_members,docstrings) ])

            member = members[0]
            tplargs = ", ".join([ d['type'] + " " + d['name'] for d in self.template_params + member.template_params ])
            output.out (template_member_func_doc.format (
                template = "template <{}>\n".format (tplargs) if len(tplargs) > 0 else "",
                rettype = member.s_rettype(),
                classname_prefix = classname_prefix,
                argsstring = member.s_prototypeArgs(),
                body = body
                ))

    def _attribute (self, member):
        # TODO
        pass

class Index:
    """
    This class is responsible for generating the list of all C++-usable documented elements.
    """
    def __init__ (self, input, output):
        self.tree = etree.parse (input)
        self.directory = path.dirname (input)
        self.xml_docstring = XmlDocString (self)
        self.compounds = set()
        self.references = dict()
        self.output = output

    def parseCompound (self):
        for compound in self.tree.getroot().iterchildren ("compound"):
            if compound.attrib['kind'] in ["class", "struct"]:
                obj = ClassCompound (compound, self)
                self.compounds.add (obj.id)
            elif compound.attrib['kind'] == "namespace":
                obj = NamespaceCompound (compound, self)
            self.registerReference (obj)

    def write (self):
        # Header
        from os.path import abspath, dirname
        self.output.out(template_file_header.format (path = dirname(abspath(__file__))))
        # Implement template specialization
        for id in self.compounds:
            compound = self.references[id]
            compound.write(self.output)
        # Footer
        self.output.out(template_file_footer)

    def registerReference (self, obj, overwrite=True):
        if obj.id in self.references:
            if obj.name != self.references[obj.id].name:
                self.output.err ("!!!! Compounds " + obj.id + " already exists.", obj.name, self.references[obj.id].name)
            else:
                self.output.err ("Reference " + obj.id + " already exists.", obj.name)
            if not overwrite: return
        self.references[obj.id] = obj

    def hasref (self, id):
        return (id in self.references)

    def getref (self, id):
        return self.references[id]

class OutputStreams(object):
    def __init__ (self, output, error, errorPrefix = ""):
        self._out = output
        self._err = error
        self.errorPrefix = errorPrefix
    def out(self, *args):
        print (*args, file=self._out)
    def err(self, *args):
        print (self.errorPrefix, *args, file=self._err)

if __name__ == "__main__":
    index = Index (input = sys.argv[1],
            output = OutputStreams (sys.stdout, sys.stderr))
    index.parseCompound()
    index.write()
