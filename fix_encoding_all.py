# -*- coding: utf-8 -*-
"""Batch encoding fixer: walk a directory tree and fix all .html files.

Three-pass approach:
  1. Decode: try UTF-8, fall back to CP1252 for raw Latin-1 files.
  2. FFFD recovery: replace U+FFFD sequences using contextual patterns from
     this LDI site's known vocabulary (navigation, address, product terms).
  3. Mojibake cleanup: fix Ã© / ÃƒÂ£ patterns that survived double-encoding.

Also injects <meta charset="utf-8"> if missing, so browsers stop guessing
the encoding and displaying U+FFFD as ï¿½.
"""

import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Mojibake replacement table (UTF-8 bytes misread as Latin-1/CP1252)
# ---------------------------------------------------------------------------
REPLACEMENTS = [
    # Double mojibake via CP1252 (ƒ U+0192 is the tell-tale sign)
    ('ÃƒÂ£', 'ã'), ('ÃƒÂ¡', 'á'), ('ÃƒÂ©', 'é'), ('ÃƒÂª', 'ê'),
    ('ÃƒÂ§', 'ç'), ('ÃƒÂ³', 'ó'), ('ÃƒÂº', 'ú'), ('ÃƒÂ¢', 'â'),
    ('ÃƒÂµ', 'õ'), ('ÃƒÂ´', 'ô'), ('ÃƒÂ¼', 'ü'), ('ÃƒÂ®', 'î'),
    ('ÃƒÂ¯', 'ï'), ('ÃƒÂ±', 'ñ'), ('ÃƒÂ­', 'í'),
    ('Ãƒâ€¡', 'Ç'),
    ('Ãƒ"', 'Ó'),
    # Single mojibake
    ('Ã³', 'ó'), ('Ã©', 'é'), ('Ã£', 'ã'), ('Ã¡', 'á'), ('Ã­', 'í'),
    ('Ãº', 'ú'), ('Ãª', 'ê'), ('Ã§', 'ç'), ('Ã¢', 'â'), ('Ãµ', 'õ'),
    ('Ã´', 'ô'), ('Ã¼', 'ü'), ('Ã®', 'î'), ('Ã¯', 'ï'), ('Ã±', 'ñ'),
    ('Ã\x81', 'Á'), ('Ã\x89', 'É'), ('Ã\x93', 'Ó'), ('Ã\x87', 'Ç'),
    ('Ã\x95', 'Õ'), ('Ã\x94', 'Ô'), ('Ã\x80', 'À'), ('Ã\x82', 'Â'),
    ('Ã\x83', 'Ã'), ('Ã\x9a', 'Ú'),
    # Â prefix patterns
    ('Â°', '°'), ('Â©', '©'), ('Â®', '®'), ('Â³', '³'), ('Â²', '²'),
]

# ---------------------------------------------------------------------------
# FFFD recovery table — longer patterns first to avoid partial overlaps.
# Built from: navigation menu (same on every LDI page), address/contact
# pages, and common Brazilian-Portuguese industrial/technical vocabulary.
# ---------------------------------------------------------------------------
F = '�'

FFFD_RECOVERIES = [
    # ---- Navigation menu (identical on every page) ----
    (f'Refrigera{F}{F}o',           'Refrigeração'),
    (f'refrigera{F}{F}o',           'refrigeração'),
    (f'Alimenta{F}{F}o',            'Alimentação'),
    (f'alimenta{F}{F}o',            'alimentação'),
    (f'Fus{F}­veis',           'Fusí­veis'),  # í + soft-hyphen → 2 FFFDs
    (f'Fus{F}{F}veis',              'Fusí­veis'),  # same, written as 2 FFFDs
    (f'Anal{F}gicos',               'Analógicos'),
    (f'Anal{F}gico',                'Analógico'),
    (f'Anal{F}gica',                'Analógica'),
    (f'G{F}s',                      'Gás'),
    (f'Sobretens{F}o',              'Sobretensão'),
    (f'Pain{F}is',                  'Painéis'),
    (f'pain{F}is',                  'painéis'),
    (f'Eletr{F}nicos',              'Eletrônicos'),
    (f'eletr{F}nicos',              'eletrônicos'),
    (f'Eletr{F}nico',               'Eletrônico'),
    (f'eletr{F}nico',               'eletrônico'),
    (f'ELETR{F}NICA',               'ELETRÔNICA'),
    (f'ELETR{F}NICOS',              'ELETRÔNICOS'),
    (f'Rel{F}s',                    'Relés'),
    (f'rel{F}s',                    'relés'),
    (f'Rel{F}',                     'Relé'),
    (f'rel{F}',                     'relé'),
    (f'S{F}lido',                   'Sólido'),
    (f's{F}lido',                   'sólido'),
    (f'Presen{F}a',                 'Presença'),
    (f'presen{F}a',                 'presença'),
    (f'Pot{F}ncia',                 'Potência'),
    (f'pot{F}ncia',                 'potência'),
    (f'C{F}meras',                  'Câmeras'),
    (f'Assist{F}ncia',              'Assistência'),
    (f'assist{F}ncia',              'assistência'),
    (f'T{F}cnica',                  'Técnica'),
    (f't{F}cnica',                  'técnica'),
    (f'T{F}cnicos',                 'Técnicos'),
    (f't{F}cnicos',                 'técnicos'),
    (f'Pol{F}tica',                 'Política'),
    (f'pol{F}tica',                 'política'),
    # ---- Titles and common content ----
    (f'Com{F}rcio',                 'Comércio'),
    (f'com{F}rcio',                 'comércio'),
    (f'Localiza{F}{F}o',           'Localização'),
    (f'localiza{F}{F}o',           'localização'),
    (f'Automa{F}{F}o',             'Automação'),
    (f'automa{F}{F}o',             'automação'),
    (f'industrializa{F}{F}o',      'industrialização'),
    (f'Industrializa{F}{F}o',      'Industrialização'),
    (f'Ant{F}nio',                  'Antônio'),
    (f'Endere{F}o',                 'Endereço'),
    (f'endere{F}o',                 'endereço'),
    (f'C{F}digo',                   'Código'),
    (f'c{F}digo',                   'código'),
    (f'C{F}digos',                  'Códigos'),
    (f'c{F}digos',                  'códigos'),
    (f'Frequ{F}ncia',               'Frequência'),
    (f'frequ{F}ncia',               'frequência'),
    (f'Manuten{F}{F}o',            'Manutenção'),
    (f'manuten{F}{F}o',            'manutenção'),
    (f'Vis{F}o',                    'Visão'),
    (f'vis{F}o',                    'visão'),
    (f'L{F}der',                    'Líder'),
    (f'l{F}der',                    'líder'),
    (f'Distribu{F}mos',             'Distribuímos'),
    (f'distribu{F}mos',             'distribuímos'),
    (f'Possu{F}mos',                'Possuímos'),
    (f'possu{F}mos',                'possuímos'),
    (f'Disponi{F}vel',              'Disponível'),
    (f'disponi{F}vel',              'disponível'),
    (f'Or{F}amento',                'Orçamento'),
    (f'or{F}amento',                'orçamento'),
    (f'Or{F}amentos',               'Orçamentos'),
    (f'or{F}amentos',               'orçamentos'),
    (f'Experi{F}ncia',              'Experiência'),
    (f'experi{F}ncia',              'experiência'),
    (f'Inspe{F}{F}o',              'Inspeção'),
    (f'inspe{F}{F}o',              'inspeção'),
    (f'Mec{F}nicos',                'Mecânicos'),
    (f'mec{F}nicos',                'mecânicos'),
    (f'Mec{F}nico',                 'Mecânico'),
    (f'mec{F}nico',                 'mecânico'),
    (f'El{F}tricos',                'Elétricos'),
    (f'el{F}tricos',                'elétricos'),
    (f'El{F}trico',                 'Elétrico'),
    (f'el{F}trico',                 'elétrico'),
    (f'Tamb{F}m',                   'Também'),
    (f'tamb{F}m',                   'também'),
    (f'Paran{F}',                   'Paraná'),
    (f'Regi{F}o',                   'Região'),
    (f'regi{F}o',                   'região'),
    (f'Programa{F}{F}o',           'Programação'),
    (f'programa{F}{F}o',           'programação'),
    # ---- Technical/product terminology ----
    (f'Tens{F}es',                  'Tensões'),
    (f'tens{F}es',                  'tensões'),
    (f'Tens{F}o',                   'Tensão'),
    (f'tens{F}o',                   'tensão'),
    (f'Sa{F}{F}das',                'Saídas'),   # í+soft-hyphen → 2 FFFDs
    (f'sa{F}{F}das',                'saídas'),
    (f'Sa{F}{F}da',                 'Saída'),
    (f'sa{F}{F}da',                 'saída'),
    (f'sa{F}{F}da',                 'saída'),
    (f'Sa{F}das',                   'Saídas'),   # without soft-hyphen fallback
    (f'sa{F}das',                   'saídas'),
    (f'Sa{F}da',                    'Saída'),
    (f'sa{F}da',                    'saída'),
    (f'Fun{F}{F}es',               'Funções'),
    (f'fun{F}{F}es',               'funções'),
    (f'Fun{F}{F}o',                'Função'),
    (f'fun{F}{F}o',                'função'),
    (f'Opera{F}{F}o',              'Operação'),
    (f'opera{F}{F}o',              'operação'),
    (f'Resolu{F}{F}o',             'Resolução'),
    (f'resolu{F}{F}o',             'resolução'),
    (f'Precis{F}o',                 'Precisão'),
    (f'precis{F}o',                 'precisão'),
    (f'Configura{F}{F}o',          'Configuração'),
    (f'configura{F}{F}o',          'configuração'),
    (f'Comunica{F}{F}o',           'Comunicação'),
    (f'comunica{F}{F}o',           'comunicação'),
    (f'Utiliza{F}{F}o',            'Utilização'),
    (f'utiliza{F}{F}o',            'utilização'),
    (f'Indica{F}{F}o',             'Indicação'),
    (f'indica{F}{F}o',             'indicação'),
    (f'Medi{F}{F}o',               'Medição'),
    (f'medi{F}{F}o',               'medição'),
    (f'Prote{F}{F}o',              'Proteção'),
    (f'prote{F}{F}o',              'proteção'),
    (f'Liga{F}{F}o',               'Ligação'),
    (f'liga{F}{F}o',               'ligação'),
    (f'Calibra{F}{F}o',            'Calibração'),
    (f'calibra{F}{F}o',            'calibração'),
    (f'Sele{F}{F}o',               'Seleção'),
    (f'sele{F}{F}o',               'seleção'),
    (f'Amplia{F}{F}o',             'Ampliação'),
    (f'amplia{F}{F}o',             'ampliação'),
    (f'Sinaliza{F}{F}o',           'Sinalização'),
    (f'sinaliza{F}{F}o',           'sinalização'),
    (f'Instala{F}{F}o',            'Instalação'),
    (f'instala{F}{F}o',            'instalação'),
    (f'Conex{F}o',                  'Conexão'),
    (f'conex{F}o',                  'conexão'),
    (f'Expans{F}o',                 'Expansão'),
    (f'expans{F}o',                 'expansão'),
    (f'Press{F}o',                  'Pressão'),
    (f'press{F}o',                  'pressão'),
    (f'Vers{F}o',                   'Versão'),
    (f'vers{F}o',                   'versão'),
    (f'Posi{F}{F}o',               'Posição'),
    (f'posi{F}{F}o',               'posição'),
    (f'Habilita{F}{F}o',           'Habilitação'),
    (f'habilita{F}{F}o',           'habilitação'),
    (f'Refer{F}ncia',               'Referência'),
    (f'refer{F}ncia',               'referência'),
    (f'Dist{F}ncia',                'Distância'),
    (f'dist{F}ncia',                'distância'),
    (f'Dimens{F}es',                'Dimensões'),
    (f'dimens{F}es',                'dimensões'),
    (f'Invers{F}res',               'Inversores'),
    (f'invers{F}res',               'inversores'),
    (f'Invers{F}r',                 'Inversor'),
    (f'invers{F}r',                 'inversor'),
    (f'M{F}ximas',                  'Máximas'),
    (f'm{F}ximas',                  'máximas'),
    (f'M{F}xima',                   'Máxima'),
    (f'm{F}xima',                   'máxima'),
    (f'M{F}ximos',                  'Máximos'),
    (f'm{F}ximos',                  'máximos'),
    (f'M{F}ximo',                   'Máximo'),
    (f'm{F}ximo',                   'máximo'),
    (f'M{F}nimas',                  'Mínimas'),
    (f'm{F}nimas',                  'mínimas'),
    (f'M{F}nima',                   'Mínima'),
    (f'm{F}nima',                   'mínima'),
    (f'M{F}nimos',                  'Mínimos'),
    (f'm{F}nimos',                  'mínimos'),
    (f'M{F}nimo',                   'Mínimo'),
    (f'm{F}nimo',                   'mínimo'),
    (f'M{F}dulo',                   'Módulo'),
    (f'm{F}dulo',                   'módulo'),
    (f'N{F}vel',                    'Nível'),
    (f'n{F}vel',                    'nível'),
    (f'N{F}mero',                   'Número'),
    (f'n{F}mero',                   'número'),
    (f'S{F}rie',                    'Série'),
    (f's{F}rie',                    'série'),
    (f'Pneum{F}tico',               'Pneumático'),
    (f'pneum{F}tico',               'pneumático'),
    (f'{F}reas',                    'áreas'),
    (f'{F}rea',                     'área'),
    # ---- Very common Portuguese words ----
    (f'n{F}o',                      'não'),
    (f'N{F}o',                      'Não'),
    (f'satisfa{F}{F}o',            'satisfação'),
    (f'Satisfa{F}{F}o',            'Satisfação'),
    (f'ser{F}o',                    'serão'),
    (f'Ser{F}o',                    'Serão'),
    (f'ser{F}',                     'será'),   # á → FFFD (e.g. "será uma grande satisfação")
    (f'Guich{F}o',                  'Guichão'),
    (f'guich{F}o',                  'guichão'),
    (f'Guich{F}',                   'Guichê'),  # ê → FFFD in product names "Guichê 1/2"
    (f'guich{F}',                   'guichê'),
    (f'hor{F}rio',                  'horário'),
    (f'Hor{F}rio',                  'Horário'),
    (f'atend{F}-lo',                'atendê-lo'),
    (f'atend{F}-la',                'atendê-la'),
    (f'Acoplador {F} Rel',          'Acoplador – Rel'),  # CP1252 en dash 0x96 between spaces
    (f'{F}s ',                      'às '),              # à → FFFD before 's' (e.g. "às 12:00")
    # ---- Technical terms with í + soft-hyphen (2 FFFDs) ----
    (f'Fus{F}{F}vel',               'Fusível'),
    (f'fus{F}{F}vel',               'fusível'),
    (f'D{F}{F}gitos',               'Dígitos'),
    (f'd{F}{F}gitos',               'dígitos'),
    (f'D{F}{F}gito',                'Dígito'),
    (f'd{F}{F}gito',                'dígito'),
    (f'Potenci{F}{F}metro',         'Potenciômetro'),
    (f'potenci{F}{F}metro',         'potenciômetro'),
    (f'Potenci{F}metro',            'Potenciômetro'),   # ô → single FFFD (0xF4 + non-continuation)
    (f'potenci{F}metro',            'potenciômetro'),
    (f'N{F}{F}vel',                 'Nível'),          # fallback for soft-hyphen í
    (f'n{F}{F}vel',                 'nível'),
    (f'M{F}{F}nimo',                'Mínimo'),
    (f'm{F}{F}nimo',                'mínimo'),
    (f'M{F}{F}nimos',               'Mínimos'),
    (f'm{F}{F}nimos',               'mínimos'),
    (f'M{F}{F}nima',                'Mínima'),
    (f'm{F}{F}nima',                'mínima'),
    # ---- Other ----
    (f'di{F}metro',                 'diâmetro'),
    (f'Di{F}metro',                 'Diâmetro'),
    (f'Isola{F}{F}o',              'Isolação'),
    (f'isola{F}{F}o',              'isolação'),
    (f'Galv{F}nico',                'Galvânico'),
    (f'galv{F}nico',                'galvânico'),
    (f'Trif{F}sico',                'Trifásico'),
    (f'trif{F}sico',                'trifásico'),
    (f'Trif{F}sica',                'Trifásica'),
    (f'trif{F}sica',                'trifásica'),
    (f'ajust{F}vel',                'ajustável'),
    (f'Ajust{F}vel',                'Ajustável'),
    (f'{F}tica',                    'ética'),
    (f'{F}nico',                    'único'),
    (f'{F}nica',                    'única'),
]


def _inject_charset(text: str) -> str:
    """Insert <meta charset="utf-8"> after <head> if no charset is declared."""
    if re.search(r'charset', text, re.IGNORECASE):
        return text
    return re.sub(
        r'(<head[^>]*>)',
        r'\1\n\t\t\t<meta charset="utf-8">',
        text,
        count=1,
        flags=re.IGNORECASE,
    )


def fix_file(path: Path) -> tuple[str, str]:
    """
    Returns (status, detail):
      fixed-cp1252        — was Latin-1, saved as UTF-8
      fixed-mojibake      — had Ã-style patterns, corrected
      fixed-fffd          — all FFFD chars recovered via context table
      partial-fffd N      — N FFFD chars remain after partial recovery
      has-fffd N          — N FFFD chars, no recoveries matched at all
      ok                  — already clean, no changes (charset meta added if missing)
      error               — could not process
    """
    try:
        data = path.read_bytes()
    except OSError as e:
        return 'error', str(e)

    reencoded = False
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        try:
            text = data.decode('cp1252')
        except Exception as e:
            return 'error', str(e)
        reencoded = True

    original = text

    # Pass 1: FFFD recovery (contextual patterns)
    for wrong, right in FFFD_RECOVERIES:
        text = text.replace(wrong, right)

    # Pass 2: mojibake cleanup
    for wrong, right in REPLACEMENTS:
        text = text.replace(wrong, right)

    # Pass 3: charset meta injection
    text = _inject_charset(text)

    changed = text != original
    remaining_fffd = text.count('�')

    if not changed and not reencoded:
        return 'ok', ''

    try:
        path.write_text(text, encoding='utf-8')
    except OSError as e:
        return 'error', str(e)

    initial_fffd = original.count('�')
    recovered = initial_fffd - remaining_fffd

    if reencoded and not initial_fffd:
        return 'fixed-cp1252', ''
    if reencoded:
        if remaining_fffd:
            return 'fixed-cp1252+partial-fffd', f'{remaining_fffd} remain'
        return 'fixed-cp1252+fffd', ''
    if initial_fffd and remaining_fffd == 0:
        return 'fixed-fffd', f'{recovered} recovered'
    if initial_fffd and recovered:
        return 'partial-fffd', f'{recovered} recovered, {remaining_fffd} remain'
    if initial_fffd:
        return 'has-fffd', f'{remaining_fffd} unrecoverable'
    return 'fixed-mojibake', ''


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('rip')

    if root.is_file():
        files = [root]
    else:
        files = sorted(root.rglob('*.html'))

    counts: dict[str, int] = {}
    for path in files:
        status, detail = fix_file(path)
        key = status.split('+')[0].split(' ')[0]
        counts[key] = counts.get(key, 0) + 1
        if status != 'ok':
            label = f'{status}  {detail}'.rstrip()
            print(f'{label:<44}  {path}')

    total = sum(counts.values())
    print(f'\n{total} files scanned')
    for k, v in sorted(counts.items()):
        print(f'  {k:<28} {v}')


if __name__ == '__main__':
    main()
