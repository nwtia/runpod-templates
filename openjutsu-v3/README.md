# NWTIA - OpenJutsu V3

## Ouvrir OpenJutsu
1. Lance le pod avec ce template.
2. Dans RunPod, ouvre ses **Logs**.
3. Ouvre le lien sur la ligne **OUVRIR OPENJUTSU**.
4. Garde la page en favori pour les prochaines fois.

Aucune cle SSH, aucune commande et aucun mot de passe a saisir. Le lien prive
ouvre ta session dans le navigateur. Garde ce lien pour toi : il donne acces a
ton OpenJutsu. Pendant le premier telechargement des modeles, la page attend
et se met a jour automatiquement. Le port HTTP 8080 doit etre expose. Les videos et photos peuvent totaliser jusqu'a 2 GiB par envoi, sans recompression. Les gros envois affichent un pourcentage et passent par des blocs verifies pour traverser le proxy RunPod.

## Stockage et modeles
Environ 41 Go de modeles publics sont telecharges et verifies au premier
lancement. GPU NVIDIA 48 Go VRAM et 64 Go RAM conseilles, pilote 580+ requis.
Disque conteneur 20 Go et volume persistant 160 Go monte sur /workspace.
Les modeles, LoRA, entrees, sorties, caches et lien prive restent dans
/workspace/openjutsu. Les dependances sont preinstallees dans l'image.
Tes LoRA vont dans /workspace/openjutsu/models/loras. Le LoRA DMAD est inclus
au telechargement ; ta bibliotheque personnelle du PC n'est pas publiee.

## V3 et validation
Qwen 4B + ClipProj v3.1, descriptif Qwen 8B, H3 Ref2VA INT8, DMAD 6 pas Euler,
LoRA configurables, caches V2/V3 et protection suivie d'un objet au premier plan.
Le moteur GPU est identique a la version testee sur A100 80 Go : deux videos
publiques de 2,33 s en 864 x 480, reussies en 303,54 s a froid puis 101,50 s
avec un autre seed. Caches tracking et source reutilises. Ces tests courts ne
garantissent pas la stabilite de toutes les videos longues. Le conditionnement
patche conserve son calcul natif. Certains modes demandent d'autres modeles.
L'acces navigateur est controle par des tests CPU : refus anonyme, protection
contre les requetes d'autres sites, transferts et progression en continu.

ComfyUI et le proxy restent locaux au pod. Seule l'entree web protegee est
exposee en HTTPS par RunPod. SSH est facultatif. Aucune generation automatique.
Aucun media personnel, historique, mot de passe ou cle privee dans l'image.

