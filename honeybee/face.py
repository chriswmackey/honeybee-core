# coding: utf-8
"""Honeybee Face."""
from __future__ import division
import math

from ladybug_geometry.geometry2d import Vector2D, Point2D, Polygon2D, Mesh2D
from ladybug_geometry.geometry3d import Vector3D, Point3D, Plane, Face3D
from ladybug.color import Color

from ._basewithshade import _BaseWithShade
from .typing import clean_string, invalid_dict_error
from .properties import FaceProperties
from .facetype import face_types, get_type_from_normal, AirBoundary, Floor, RoofCeiling
from .boundarycondition import boundary_conditions, get_bc_from_position, \
    _BoundaryCondition, Outdoors, Surface, Ground
from .shade import Shade
from .aperture import Aperture
from .door import Door
import honeybee.boundarycondition as hbc
import honeybee.writer.face as writer


class Face(_BaseWithShade):
    """A single planar face.

    Args:
        identifier: Text string for a unique Face ID. Must be < 100 characters and
            not contain any spaces or special characters.
        geometry: A ladybug-geometry Face3D.
        type: Face type. Default varies depending on the direction that
            the Face geometry is points.
            RoofCeiling = pointing upward within 30 degrees
            Wall = oriented vertically within +/- 60 degrees
            Floor = pointing downward within 30 degrees
        boundary_condition: Face boundary condition (Outdoors, Ground, etc.)
            Default is Outdoors unless all vertices of the geometry lie
            below the below the XY plane, in which case it will be set to Ground.

    Properties:
        * identifier
        * display_name
        * type
        * boundary_condition
        * apertures
        * doors
        * sub_faces
        * indoor_shades
        * outdoor_shades
        * parent
        * has_parent
        * has_sub_faces
        * can_be_ground
        * geometry
        * punched_geometry
        * vertices
        * punched_vertices
        * upper_left_vertices
        * normal
        * center
        * area
        * perimeter
        * min
        * max
        * aperture_area
        * aperture_ratio
        * tilt
        * altitude
        * azimuth
        * is_exterior
        * type_color
        * bc_color
        * user_data
    """
    TYPES = face_types
    __slots__ = ('_geometry', '_parent', '_punched_geometry',
                 '_apertures', '_doors', '_type', '_boundary_condition')
    TYPE_COLORS = {
        'Wall': Color(230, 180, 60),
        'RoofCeiling': Color(128, 20, 20),
        'Floor': Color(128, 128, 128),
        'AirBoundary': Color(255, 255, 200, 100),
        'InteriorWall': Color(230, 215, 150),
        'InteriorRoofCeiling': Color(255, 128, 128),
        'InteriorFloor': Color(255, 128, 128),
        'InteriorAirBoundary': Color(255, 255, 200, 100)
    }
    BC_COLORS = {
        'Outdoors': Color(64, 180, 255),
        'Surface': Color(0, 128, 0),
        'Ground': Color(165, 82, 0),
        'Adiabatic': Color(255, 128, 128),
        'Other': Color(255, 255, 200)
    }

    def __init__(self, identifier, geometry, type=None, boundary_condition=None):
        """A single planar face."""
        _BaseWithShade.__init__(self, identifier)  # process the identifier

        # process the geometry
        assert isinstance(geometry, Face3D), \
            'Expected ladybug_geometry Face3D. Got {}'.format(geometry)
        self._geometry = geometry
        self._parent = None  # _parent will be set when the Face is added to a Room
        # initialize with no apertures/doors (they can be assigned later)
        self._punched_geometry = None
        self._apertures = []
        self._doors = []

        # initialize properties for extensions
        self._properties = FaceProperties(self)

        # set face type based on normal if not provided
        if type is not None:
            assert type in self.TYPES, '{} is not a valid face type.'.format(type)
        self._type = type or get_type_from_normal(geometry.normal)

        # set boundary condition by the relation to a zero ground plane if not provided
        self.boundary_condition = boundary_condition or \
            get_bc_from_position(geometry.boundary)

    @classmethod
    def from_dict(cls, data):
        """Initialize an Face from a dictionary.

        Args:
            data: A dictionary representation of an Face object.
        """
        try:
            # check the type of dictionary
            assert data['type'] == 'Face', 'Expected Face dictionary. ' \
                'Got {}.'.format(data['type'])

            # remove any invalid holes from the geometry
            geo_dict = data['geometry']
            if 'holes' in geo_dict and geo_dict['holes'] is not None:
                for i, hole_list in enumerate(geo_dict['holes']):
                    if len(hole_list) < 3:
                        geo_dict['holes'].pop(i)

            # first serialize it with an outdoor boundary condition
            face_type = face_types.by_name(data['face_type'])
            face = cls(data['identifier'], Face3D.from_dict(data['geometry']),
                       face_type, boundary_conditions.outdoors)
            if 'display_name' in data and data['display_name'] is not None:
                face.display_name = data['display_name']
            if 'user_data' in data and data['user_data'] is not None:
                face.user_data = data['user_data']

            # add sub-faces and shades
            if 'apertures' in data and data['apertures'] is not None:
                aps = []
                for ap in data['apertures']:
                    try:
                        aps.append(Aperture.from_dict(ap))
                    except Exception as e:
                        invalid_dict_error(ap, e)
                face.add_apertures(aps)
            if 'doors' in data and data['doors'] is not None:
                drs = []
                for dr in data['doors']:
                    try:
                        drs.append(Door.from_dict(dr))
                    except Exception as e:
                        invalid_dict_error(dr, e)
                face.add_doors(drs)
            face._recover_shades_from_dict(data)

            # get the boundary condition and assign it
            try:
                bc_class = getattr(hbc, data['boundary_condition']['type'])
                face.boundary_condition = bc_class.from_dict(data['boundary_condition'])
            except AttributeError:  # extension boundary condition; default to Outdoors
                pass

            # assign extension properties
            if data['properties']['type'] == 'FaceProperties':
                face.properties._load_extension_attr_from_dict(data['properties'])
            return face
        except Exception as e:
            cls._from_dict_error_message(data, e)

    @classmethod
    def from_vertices(cls, identifier, vertices, type=None, boundary_condition=None):
        """Create a Face from vertices with each vertex as an iterable of 3 floats.

        Note that this method is not recommended for a face with one or more holes
        since the distinction between hole vertices and boundary vertices cannot
        be derived from a single list of vertices.

        Args:
            identifier: Text string for a unique Face ID. Must be < 100 characters and
                not contain any spaces or special characters.
            vertices: A flattened list of 3 or more vertices as (x, y, z).
            type: Face type object (eg. Wall, Floor).
            boundary_condition: Boundary condition object (eg. Outdoors, Ground)
        """
        geometry = Face3D(tuple(Point3D(*v) for v in vertices))
        return cls(identifier, geometry, type, boundary_condition)

    @property
    def type(self):
        """Get or set an object for Type of Face (ie. Wall, Floor, Roof).

        Note that setting this property will reset extension attributes on this
        Face to their default values.
        """
        return self._type

    @type.setter
    def type(self, value):
        assert value in self.TYPES, '{} is not a valid face type.'.format(value)
        if isinstance(value, AirBoundary):
            assert self._apertures == [] or self._doors == [], \
                '{} cannot be assigned to a Face with Apertures or Doors.'.format(value)
        self.properties.reset_to_default()  # reset constructions/modifiers
        self._type = value

    @property
    def boundary_condition(self):
        """Get or set the boundary condition of the Face. (ie. Outdoors, Ground, etc.).
        """
        return self._boundary_condition

    @boundary_condition.setter
    def boundary_condition(self, value):
        assert isinstance(value, _BoundaryCondition), \
            'Expected BoundaryCondition. Got {}'.format(type(value))
        if self._apertures != [] or self._doors != []:
            assert isinstance(value, (Outdoors, Surface)), \
                '{} cannot be assigned to a Face with apertures or doors.'.format(value)
        self._boundary_condition = value

    @property
    def apertures(self):
        """Get a tuple of apertures in this Face."""
        return tuple(self._apertures)

    @property
    def doors(self):
        """Get a tuple of doors in this Face."""
        return tuple(self._doors)

    @property
    def sub_faces(self):
        """Get a tuple of apertures and doors in this Face."""
        return tuple(self._apertures + self._doors)

    @property
    def parent(self):
        """Get the parent Room if assigned. None if not assigned."""
        return self._parent

    @property
    def has_parent(self):
        """Get a boolean noting whether this Face has a parent Room."""
        return self._parent is not None

    @property
    def has_sub_faces(self):
        """Get a boolean noting whether this Face has Apertures or Doors."""
        return not (self._apertures == [] and self._doors == [])

    @property
    def can_be_ground(self):
        """Get a boolean for whether this Face can support a Ground boundary condition.
        """
        return self._apertures == [] and self._doors == [] \
            and not isinstance(self._type, AirBoundary)

    @property
    def geometry(self):
        """Get a ladybug_geometry Face3D object representing the Face.

        Note that this Face3D only represents the parent face and does not have any
        holes cut in it for apertures or doors.
        """
        return self._geometry

    @property
    def punched_geometry(self):
        """Get a Face3D object with holes cut in it for apertures and doors.
        """
        if self._punched_geometry is None:
            _sub_faces = tuple(sub_f.geometry for sub_f in self._apertures + self._doors)
            if len(_sub_faces) != 0:
                self._punched_geometry = Face3D.from_punched_geometry(
                    self._geometry, _sub_faces)
            else:
                self._punched_geometry = self._geometry
        return self._punched_geometry

    @property
    def vertices(self):
        """Get a list of vertices for the face (in counter-clockwise order).

        Note that these vertices only represent the outer boundary of the face
        and do not account for holes cut in the face by apertures or doors.
        """
        return self._geometry.vertices

    @property
    def punched_vertices(self):
        """Get a list of vertices with holes cut in it for apertures and doors.

        Note that some vertices will be repeated since the vertices effectively
        trace out a single boundary around the whole shape, winding inward to cut
        out the holes. This property should be used  when exporting to Radiance.
        """
        return self.punched_geometry.vertices

    @property
    def upper_left_vertices(self):
        """Get a list of vertices starting from the upper-left corner.

        This property obeys the same rules as the vertices property but always starts
        from the upper-left-most vertex.  This property should be used when exporting to
        EnergyPlus / OpenStudio.
        """
        return self._geometry.upper_left_counter_clockwise_vertices

    @property
    def normal(self):
        """Get a Vector3D for the direction in which the face is pointing.
        """
        return self._geometry.normal

    @property
    def center(self):
        """Get a ladybug_geometry Point3D for the center of the face.

        Note that this is the center of the bounding rectangle around this geometry
        and not the area centroid.
        """
        return self._geometry.center

    @property
    def area(self):
        """Get the area of the face."""
        return self._geometry.area

    @property
    def perimeter(self):
        """Get the perimeter of the face. This includes the length of holes in the face.
        """
        return self._geometry.perimeter

    @property
    def min(self):
        """Get a Point3D for the minimum of the bounding box around the object."""
        all_geo = self._outdoor_shades + self._indoor_shades
        all_geo.extend(self._apertures)
        all_geo.extend(self._doors)
        all_geo.append(self.geometry)
        return self._calculate_min(all_geo)

    @property
    def max(self):
        """Get a Point3D for the maximum of the bounding box around the object."""
        all_geo = self._outdoor_shades + self._indoor_shades
        all_geo.extend(self._apertures)
        all_geo.extend(self._doors)
        all_geo.append(self.geometry)
        return self._calculate_max(all_geo)

    @property
    def aperture_area(self):
        """Get the combined area of the face's apertures."""
        return sum([ap.area for ap in self._apertures])

    @property
    def aperture_ratio(self):
        """Get a number between 0 and 1 for the area ratio of the apertures to the face.
        """
        return self.aperture_area / self.area

    @property
    def tilt(self):
        """Get the tilt of the geometry between 0 (up) and 180 (down)."""
        return math.degrees(self._geometry.tilt)

    @property
    def altitude(self):
        """Get the altitude of the geometry between +90 (up) and -90 (down)."""
        return math.degrees(self._geometry.altitude)

    @property
    def azimuth(self):
        """Get the azimuth of the geometry, between 0 and 360.

        Given Y-axis as North, 0 = North, 90 = East, 180 = South, 270 = West
        This will be zero if the Face3D is perfectly horizontal.
        """
        return math.degrees(self._geometry.azimuth)

    @property
    def is_exterior(self):
        """Get a boolean for whether this object has an Outdoors boundary condition.
        """
        return isinstance(self.boundary_condition, Outdoors)

    @property
    def type_color(self):
        """Get a Color to be used in visualizations by type."""
        ts = self.type.name if isinstance(self.boundary_condition, (Outdoors, Ground)) \
            else 'Interior{}'.format(self.type.name)
        return self.TYPE_COLORS[ts]

    @property
    def bc_color(self):
        """Get a Color to be used in visualizations by boundary condition."""
        try:
            return self.BC_COLORS[self.boundary_condition.name]
        except KeyError:  # extension boundary condition
            return self.BC_COLORS['Other']

    def horizontal_orientation(self, north_vector=Vector2D(0, 1)):
        """Get a number between 0 and 360 for the orientation of the face in degrees.

        0 = North, 90 = East, 180 = South, 270 = West

        Args:
            north_vector: A ladybug_geometry Vector2D for the north direction.
                Default is the Y-axis (0, 1).
        """
        return math.degrees(
            north_vector.angle_clockwise(Vector2D(self.normal.x, self.normal.y)))

    def cardinal_direction(self, north_vector=Vector2D(0, 1)):
        """Get text description for the cardinal direction that the face is pointing.

        Will be one of the following: ('North', 'NorthEast', 'East', 'SouthEast',
        'South', 'SouthWest', 'West', 'NorthWest').

        Args:
            north_vector: A ladybug_geometry Vector2D for the north direction.
                Default is the Y-axis (0, 1).
        """
        orient = self.horizontal_orientation(north_vector)
        orient_text = ('North', 'NorthEast', 'East', 'SouthEast', 'South',
                       'SouthWest', 'West', 'NorthWest')
        angles = (22.5, 67.5, 112.5, 157.5, 202.5, 247.5, 292.5, 337.5)
        for i, ang in enumerate(angles):
            if orient < ang:
                return orient_text[i]
        return orient_text[0]

    def add_prefix(self, prefix):
        """Change the identifier of this object and child objects by inserting a prefix.

        This is particularly useful in workflows where you duplicate and edit
        a starting object and then want to combine it with the original object
        into one Model (like making a model of repeated rooms) since all objects
        within a Model must have unique identifiers.

        Args:
            prefix: Text that will be inserted at the start of this object's
                (and child objects') identifier and display_name. It is recommended
                that this prefix be short to avoid maxing out the 100 allowable
                characters for honeybee identifiers.
        """
        self._identifier = clean_string('{}_{}'.format(prefix, self.identifier))
        self.display_name = '{}_{}'.format(prefix, self.display_name)
        self.properties.add_prefix(prefix)
        for ap in self._apertures:
            ap.add_prefix(prefix)
        for dr in self._doors:
            dr.add_prefix(prefix)
        self._add_prefix_shades(prefix)
        if isinstance(self._boundary_condition, Surface):
            new_bc_objs = (clean_string('{}_{}'.format(prefix, adj_name)) for adj_name
                           in self._boundary_condition._boundary_condition_objects)
            self._boundary_condition = Surface(new_bc_objs, False)

    def remove_sub_faces(self):
        """Remove all apertures and doors from the face."""
        self.remove_apertures()
        self.remove_doors()

    def remove_apertures(self):
        """Remove all apertures from the face."""
        for aperture in self._apertures:
            aperture._parent = None
        self._apertures = []
        self._punched_geometry = None  # reset so that it can be re-computed

    def remove_doors(self):
        """Remove all doors from the face."""
        for door in self._apertures:
            door._parent = None
        self._doors = []
        self._punched_geometry = None  # reset so that it can be re-computed

    def add_aperture(self, aperture):
        """Add an Aperture to this face.

        This method does not check the co-planarity between this Face and the
        Aperture or whether the Aperture has all vertices within the boundary of
        this Face. To check this, the Face3D.is_sub_face() method can be used
        with the Aperture and Face geometry before using this method or the
        are_sub_faces_valid() method can be used afterwards.

        Args:
            aperture: An Aperture to add to this face.
        """
        assert isinstance(aperture, Aperture), \
            'Expected Aperture. Got {}.'.format(type(aperture))
        self._acceptable_sub_face_check(Aperture)
        aperture._parent = self
        if self.normal.angle(aperture.normal) > math.pi / 2:  # reversed normal
            aperture._geometry = aperture._geometry.flip()
        self._apertures.append(aperture)
        self._punched_geometry = None  # reset so that it can be re-computed

    def add_door(self, door):
        """Add a Door to this face.

        This method does not check the co-planarity between this Face and the
        Door or whether the Door has all vertices within the boundary of
        this Face. To check this, the Face3D.is_sub_face() method can be used
        with the Door and Face geometry before using this method or the
        are_sub_faces_valid() method can be used afterwards.

        Args:
            door: A Door to add to this face.
        """
        assert isinstance(door, Door), \
            'Expected Door. Got {}.'.format(type(door))
        self._acceptable_sub_face_check(Door)
        door._parent = self
        if self.normal.angle(door.normal) > math.pi / 2:  # reversed normal
            door._geometry = door._geometry.flip()
        self._doors.append(door)
        self._punched_geometry = None  # reset so that it can be re-computed

    def add_sub_face(self, sub_face):
        """Add an Apertures or Doors to this face."""
        if isinstance(sub_face, Aperture):
            self.add_aperture(sub_face)
        else:
            self.add_door(sub_face)

    def add_apertures(self, apertures):
        """Add a list of Apertures to this face."""
        for aperture in apertures:
            self.add_aperture(aperture)

    def add_doors(self, doors):
        """Add a list of Doors to this face."""
        for door in doors:
            self.add_door(door)

    def add_sub_faces(self, sub_faces):
        """Add a list of Apertures and/or Doors to this face."""
        for sub_f in sub_faces:
            self.add_sub_face(sub_f)

    def replace_apertures(self, apertures):
        """Replace all sub-faces assigned to this Face with a new list of Apertures."""
        self.remove_sub_faces()
        self.add_apertures(apertures)

    def set_adjacency(self, other_face, tolerance=0.01):
        """Set this face adjacent to another and set the other face adjacent to this one.

        Note that this method does not verify whether the other_face geometry is
        co-planar or compatible with this one so it is recommended that either the
        Face3D.is_centered_adjacent() or the Face3D.is_geometrically_equivalent()
        method be used with this face geometry and the other_face geometry
        before using this method in order to verify these criteria.

        However, this method will use the proximity of apertures and doors within
        the input tolerance to determine which of the sub faces in the other_face
        are adjacent to the ones in this face. An exception will be thrown if not
        all sub-faces can be matched.

        Args:
            other_face: Another Face object to be set adjacent to this one.
            tolerance: The minimum distance between the center of two aperture
                geometries at which they are considered adjacent. Default: 0.01,
                suitable for objects in meters.

        Returns:
            A dictionary of adjacency information with the following keys

            -   adjacent_apertures - A list of tuples with each tuple containing 2
                objects for Apertures paired in the process of solving adjacency.

            -   adjacent_doors - A list of tuples with each tuple containing 2
                objects for Doors paired in the process of solving adjacency.
        """
        # check the inputs and the ability of the faces to be adjacent
        assert isinstance(other_face, Face), \
            'Expected honeybee Face. Got {}.'.format(type(other_face))

        # set the boundary conditions of the faces
        self._boundary_condition = boundary_conditions.surface(other_face)
        other_face._boundary_condition = boundary_conditions.surface(self)

        adj_info = {'adjacent_apertures': [], 'adjacent_doors': []}

        # set the apertures to be adjacent to one another
        if len(self._apertures) != len(other_face._apertures):
            msg = 'Number of apertures does not match between {} and {}.'.format(
                self.display_name, other_face.display_name)
            if self.has_parent and other_face.has_parent:
                msg = '{} Relevant rooms: {}, {}'.format(
                    msg, self.parent.display_name, other_face.parent.display_name)
            raise AssertionError(msg)
        if len(self._apertures) > 0:
            found_adjacencies = 0
            for aper_1 in self._apertures:
                for aper_2 in other_face._apertures:
                    if aper_1.center.distance_to_point(aper_2.center) <= tolerance:
                        aper_1.set_adjacency(aper_2)
                        adj_info['adjacent_apertures'].append((aper_1, aper_2))
                        found_adjacencies += 1
                        break
            if len(self._apertures) != found_adjacencies:
                msg = 'Not all apertures of {} were found to be adjacent to ' \
                    'apertures in {}.'.format(self.display_name, other_face.display_name)
                if self.has_parent and other_face.has_parent:
                    msg = '{} Relevant rooms: {}, {}'.format(
                        msg, self.parent.display_name, other_face.parent.display_name)
                raise AssertionError(msg)

        # set the doors to be adjacent to one another
        assert len(self._doors) == len(other_face._doors), \
            'Number of doors does not match between {} and {}.'.format(
                self.display_name, other_face.display_name)
        if len(self._doors) > 0:
            found_adjacencies = 0
            for door_1 in self._doors:
                for door_2 in other_face._doors:
                    if door_1.center.distance_to_point(door_2.center) <= tolerance:
                        door_1.set_adjacency(door_2)
                        adj_info['adjacent_doors'].append((door_1, door_2))
                        found_adjacencies += 1
                        break
            if len(self._doors) != found_adjacencies:
                msg = 'Not all doors of {} were found to be adjacent to ' \
                    'doors in {}.'.format(self.display_name, other_face.display_name)
                if self.has_parent and other_face.has_parent:
                    msg = '{} Relevant rooms: {}, {}'.format(
                        msg, self.parent.display_name, other_face.parent.display_name)
                raise AssertionError(msg)

        return adj_info

    def rectangularize_apertures(
            self, subdivision_distance=None, max_separation=None,
            merge_all=False, tolerance=0.01, angle_tolerance=1.0):
        """Convert all Apertures on this Face to be rectangular.

        This is useful when exporting to simulation engines that only accept
        rectangular window geometry. This method will always result ing Rooms where
        all Apertures are rectangular. However, if the subdivision_distance is not
        set, some Apertures may extend past the parent Face or may collide with
        one another.

        Args:
            subdivision_distance: A number for the resolution at which the
                non-rectangular Apertures will be subdivided into smaller
                rectangular units. Specifying a number here ensures that the
                resulting rectangular Apertures do not extend past the parent
                Face or collide with one another. If None, all non-rectangular
                Apertures will be rectangularized by taking the bounding rectangle
                around the Aperture. (Default: None).
            max_separation: A number for the maximum distance between non-rectangular
                Apertures at which point the Apertures will be merged into a single
                rectangular geometry. This is often helpful when there are several
                triangular Apertures that together make a rectangle when they are
                merged across their frames. In such cases, this max_separation
                should be set to a value that is slightly larger than the window frame.
                If None, no merging of Apertures will happen before they are
                converted to rectangles. (Default: None).
            merge_all: Boolean to note whether all apertures should be merged before
                they are rectangularized. If False, only non-rectangular apertures
                will be merged before rectangularization. Note that this argument
                has no effect when the max_separation is None. (Default: False).
            tolerance: The maximum difference between point values for them to be
                considered equivalent. (Default: 0.01, suitable for objects in meters).
            angle_tolerance: The max angle in degrees that the corners of the
                rectangle can differ from a right angle before it is not
                considered a rectangle. (Default: 1).

        Returns:
            True if the Apertures were changed. False if they were unchanged.
        """
        # sort the rectangular and non-rectangular apertures
        apertures = self._apertures
        if len(apertures) == 0:
            return False
        tol, ang_tol = tolerance, math.radians(angle_tolerance)
        rect_aps, non_rect_aps, non_rect_geos = [], [], []
        for aperture in apertures:
            try:
                clean_geo = aperture.geometry.remove_colinear_vertices(tol)
            except AssertionError:  # degenerate Aperture to be ignored
                continue
            if max_separation is None or not merge_all:
                if clean_geo.polygon2d.is_rectangle(ang_tol):
                    rect_aps.append(aperture)
                else:
                    non_rect_aps.append(aperture)
                    non_rect_geos.append(clean_geo)
            else:
                non_rect_aps.append(aperture)
                non_rect_geos.append(clean_geo)
        if not non_rect_geos:  # nothing to be rectangularized
            return False

        # reset boundary conditions to outdoors so new apertures can be added
        if not isinstance(self.boundary_condition, Outdoors):
            self.boundary_condition = boundary_conditions.outdoors
            for ap in rect_aps:
                ap.boundary_condition = boundary_conditions.outdoors
        edits_occurred = False

        # try to merge the non-rectangular apertures if a max_separation is specified
        ref_plane = self._reference_plane(ang_tol)
        if max_separation is not None:
            if merge_all or (not merge_all and len(non_rect_geos) > 1):
                edits_occurred = True
                if max_separation <= tol:  # just join the Apertures at the tolerance
                    non_rect_geos = Face3D.join_coplanar_faces(non_rect_geos, tol)
                else:  # join the Apertures using the max_separation
                    # get polygons for the faces that all lie within the same plane
                    face_polys = []
                    for fg in non_rect_geos:
                        verts2d = tuple(ref_plane.xyz_to_xy(_v) for _v in fg.boundary)
                        face_polys.append(Polygon2D(verts2d))
                        if fg.has_holes:
                            for hole in fg.holes:
                                verts2d = tuple(ref_plane.xyz_to_xy(_v) for _v in hole)
                                face_polys.append(Polygon2D(verts2d))
                    # get the joined boundaries around the Polygon2D
                    joined_bounds = Polygon2D.gap_crossing_boundary(
                        face_polys, max_separation, tolerance)
                    # convert the boundary polygons back to Face3D
                    if len(joined_bounds) == 1:  # can be represented with a single Face3D
                        verts3d = tuple(ref_plane.xy_to_xyz(_v) for _v in joined_bounds[0])
                        non_rect_geos = [Face3D(verts3d, plane=ref_plane)]
                    elif len(joined_bounds) == 0:  # everything was invalid
                        non_rect_geos = []
                    else:  # need to separate holes from distinct Face3Ds
                        bound_faces = []
                        for poly in joined_bounds:
                            verts3d = tuple(ref_plane.xy_to_xyz(_v) for _v in poly)
                            bound_faces.append(Face3D(verts3d, plane=ref_plane))
                        non_rect_geos = Face3D.merge_faces_to_holes(bound_faces, tolerance)
                clean_aps = []
                for ap_geo in non_rect_geos:
                    try:
                        clean_aps.append(ap_geo.remove_colinear_vertices(tol))
                    except AssertionError:  # degenerate Aperture to be ignored
                        continue
                non_rect_geos = clean_aps

        # convert the remaining Aperture geometries to rectangles
        if subdivision_distance is None:  # just take the bounding rectangle
            edits_occurred = True
            # get the bounding rectangle around all of the geometries
            ap_geos = []
            for ap_geo in non_rect_geos:
                if ap_geo.polygon2d.is_rectangle(ang_tol):
                    ap_geos.append(ap_geo)  # catch rectangles found in merging
                    continue
                geo_2d = Polygon2D([ref_plane.xyz_to_xy(v) for v in ap_geo.vertices])
                g_min, g_max = geo_2d.min, geo_2d.max
                base, hgt = g_max.x - g_min.x, g_max.y - g_min.y
                bound_poly = Polygon2D.from_rectangle(g_min, Vector2D(0, 1), base, hgt)
                geo_3d = Face3D([ref_plane.xy_to_xyz(v) for v in bound_poly.vertices])
                ap_geos.append(geo_3d)
            non_rect_geos = ap_geos

        # create Aperture objects from all of the merged geometries
        if not edits_occurred:
            new_aps = non_rect_aps
        else:
            new_aps = []
            for i, ap_face in enumerate(non_rect_geos):
                exist_ap = None
                for old_ap in non_rect_aps:
                    if old_ap.center.is_equivalent(ap_face.center, tolerance):
                        exist_ap = old_ap
                        break
                if exist_ap is None:  # could not be matched; just make a new aperture
                    new_ap = Aperture('{}_RG{}'.format(self.identifier, i), ap_face)
                else:
                    new_ap = Aperture(exist_ap.identifier, ap_face,
                                      is_operable=exist_ap.is_operable)
                    new_ap.display_name = '{}_{}'.format(exist_ap.display_name, i)
                new_aps.append(new_ap)

        # we can just add the apertures if there's no subdivision going on
        if subdivision_distance is None:
            # remove any Apertures that are overlapping
            all_aps = rect_aps + new_aps
            all_aps = self._remove_overlapping_sub_faces(all_aps, tolerance)
            self.remove_apertures()
            self.add_apertures(all_aps)
            return True

        # if distance is provided, subdivide the apertures into strips
        new_ap_objs = []
        for ap_obj in new_aps:
            ap_geo = ap_obj.geometry
            if ap_geo.polygon2d.is_rectangle(ang_tol):
                new_ap_objs.append(ap_obj)  # catch rectangles found in merging
                continue
            # create a mesh grid over the Aperture in the reference plane
            geo_2d = Polygon2D([ref_plane.xyz_to_xy(v) for v in ap_geo.vertices])
            try:
                grid = Mesh2D.from_polygon_grid(
                    geo_2d, subdivision_distance, subdivision_distance, False)
            except AssertionError:  # Aperture smaller than resolution; ignore
                continue

            # group face by y value. All the rows will be merged together
            vertices = grid.vertices
            groups = {}
            start_y = None
            last_y = vertices[grid.faces[0][0]].y
            for i, face in enumerate(grid.faces):
                min_2d = vertices[face[0]]
                for xy in groups:
                    if abs(min_2d.x - xy[0]) < tolerance and \
                            abs(min_2d.y - last_y) < tolerance:
                        groups[(xy[0], start_y)].append(face)
                        break
                else:
                    start_y = min_2d.y
                    groups[(min_2d.x, start_y)] = [face]
                last_y = vertices[face[3]].y

            # get the max and min of each group
            sorted_groups = []
            for group in groups.values():
                # find min_2d and max_2d for each group
                min_2d = vertices[group[0][0]]
                max_2d = vertices[group[-1][2]]
                sorted_groups.append({'min': min_2d, 'max': max_2d})

            def _get_last_row(groups, start=0):
                """An internal function to return the index for the last row that can be
                merged with the start row that is passed to this function.

                This function compares the min and max x and y values for each row to see
                if they can be merged into a rectangle.
                """
                for count, group in enumerate(groups[start:]):
                    next_group = groups[count + start + 1]
                    if abs(group['min'].y - next_group['min'].y) <= tolerance \
                        and abs(group['max'].y - next_group['max'].y) <= tolerance \
                            and abs(next_group['min'].x - group['max'].x) <= tolerance:
                        continue
                    else:
                        return start + count

                return start + count + 1

            # merge the rows if they have the same number of grid cells
            sorted_groups.sort(key=lambda x: x['min'].x)
            merged_groups = []
            start_row = 0
            last_row = -1
            while last_row < len(sorted_groups):
                try:
                    last_row = _get_last_row(sorted_groups, start=start_row)
                except IndexError:
                    merged_groups.append(
                        {
                            'min': sorted_groups[start_row]['min'],
                            'max': sorted_groups[len(sorted_groups) - 1]['max']
                        }
                    )
                    break
                else:
                    merged_groups.append(
                        {
                            'min': sorted_groups[start_row]['min'],
                            'max': sorted_groups[last_row]['max']
                        }
                    )
                    if last_row == start_row:
                        # the row was not grouped with anything else
                        start_row += 1
                    else:
                        start_row = last_row + 1

            # convert the groups into rectangular strips
            for i, group in enumerate(merged_groups):
                min_2d = group['min']
                max_2d = group['max']
                base, hgt = max_2d.x - min_2d.x, max_2d.y - min_2d.y
                bound_poly = Polygon2D.from_rectangle(min_2d, Vector2D(0, 1), base, hgt)
                geo_3d = Face3D([ref_plane.xy_to_xyz(v) for v in bound_poly.vertices])
                new_ap = Aperture(
                    '{}_Glz{}'.format(ap_obj.identifier, i),
                    geo_3d, is_operable=ap_obj.is_operable)
                new_ap.display_name = '{}_{}'.format(ap_obj.display_name, i)
                new_ap_objs.append(new_ap)

        # replace the apertures with the new ones
        self.remove_apertures()
        self.add_apertures(rect_aps + new_ap_objs)
        return True

    def _reference_plane(self, angle_tolerance):
        """Get a Plane for this Face geometry derived from the Face3D plane.

        This will be oriented with the plane Y-Axis either aligned with the
        World Z or World Y, which is helpful in rectangularization.

        Args:
            angle_tolerance: The max angle in radians that Face normal can differ
                from the World Z before the Face is treated as being in the
                World XY plane.
        """
        parent_llc = self.geometry.lower_left_corner
        rel_plane = self.geometry.plane
        vertical = Vector3D(0, 0, 1)
        vert_ang = rel_plane.n.angle(vertical)
        if vert_ang <= angle_tolerance or vert_ang >= math.pi - angle_tolerance:
            proj_x = Vector3D(1, 0, 0)
        else:
            proj_y = vertical.project(rel_plane.n)
            proj_x = proj_y.rotate(rel_plane.n, math.pi / -2)

        ref_plane = Plane(rel_plane.n, parent_llc, proj_x)
        return ref_plane

    def offset_aperture_edges(self, offset_distance, tolerance=0.01):
        """Offset the edges of all apertures by a certain distance.

        This is useful for translating between interfaces that expect the window
        frame to be included within or excluded from the geometry of the Aperture.

        Note that this operation can often create Apertures that collide with
        one another or extend past the parent Face. So it may be desirable
        to run the fix_invalid_sub_faces after using this method.

        Args:
            offset_distance: Distance with which the edges of each Aperture will
                be offset from the original geometry. Positive values will
                offset the geometry outwards and negative values will offset the
                geometries inwards.
            tolerance: The minimum difference between point values for them to be
                considered the distinct. (Default: 0.01, suitable for objects
                in meters).
        """
        # convert the apertures to polygons and offset them
        new_apertures = []
        prim_pl = self.geometry.plane
        for ap in self.apertures:
            try:
                verts_2d = tuple(prim_pl.xyz_to_xy(pt) for pt in ap.geometry.boundary)
                poly = Polygon2D(verts_2d).remove_colinear_vertices(tolerance)
                off_poly = poly.offset(-offset_distance, True)
                if off_poly is not None:
                    verts_3d = tuple(prim_pl.xy_to_xyz(pt) for pt in off_poly)
                    new_ap = ap.duplicate()
                    new_ap._geometry = Face3D(verts_3d, prim_pl)
                    new_apertures.append(new_ap)
                else:
                    new_apertures.append(ap)
            except AssertionError:  # degenerate geometry to ignore
                new_apertures.append(ap)
        # assign the new apertures
        self.remove_apertures()
        self.add_apertures(new_apertures)

    def merge_neighboring_sub_faces(self, merge_distance=0.05, tolerance=0.01):
        """Merge neighboring Apertures and/or Doors on this Face together.

        This method is particularly useful for simplifying Apertures in concave
        Faces since trying to simplify such Apertures down to a ratio will
        produce a triangulated result that is not particularly clean.

        Args:
            merge_distance: Distance between Apertures and/or Doors at which point they
                will be merged into a single Aperture. When this value is less than
                or equal to the tolerance, apertures will only be merged if they
                touch one another. (Default: 0.05, suitable for objects in meters).
            tolerance: The minimum difference between point values for them to be
                considered the distinct. (Default: 0.01, suitable for objects
                in meters).
        """
        # first, check that there are Apertures to e merged
        sub_faces = self.sub_faces
        if len(sub_faces) <= 1:  # no apertures to be merged
            return

        # collect the sub-face geometries as polygons in the face plane
        clean_polys, original_objs, original_area = [], [], 0
        prim_pl = self.geometry.plane
        for sub_f in sub_faces:
            try:
                verts_2d = tuple(prim_pl.xyz_to_xy(pt) for pt in sub_f.geometry.boundary)
                poly = Polygon2D(verts_2d).remove_colinear_vertices(tolerance)
                clean_polys.append(poly)
                original_area += poly.area
                original_objs.append(sub_f)
            except AssertionError:  # degenerate geometry to ignore
                pass
        original_polys = clean_polys[:]

        # join the polygons together
        if merge_distance <= tolerance:  # only join the polygons that touch one another
            clean_polys = Polygon2D.joined_intersected_boundary(clean_polys, tolerance)
        else:
            clean_polys = Polygon2D.gap_crossing_boundary(
                clean_polys, merge_distance, tolerance)

        # assuming that the operations have edited the polygons, create new sub-faces
        new_area = sum(p.area for p in clean_polys)
        area_diff = abs(original_area - new_area)
        if len(clean_polys) != len(original_polys) or area_diff > tolerance:
            clean_polys = [poly.remove_colinear_vertices(tolerance)
                           for poly in clean_polys]
            self.remove_sub_faces()
            for i, n_poly in enumerate(clean_polys):
                new_geo = Face3D([prim_pl.xy_to_xyz(pt) for pt in n_poly], prim_pl)
                for o_poly, o_obj in zip(original_polys, original_objs):
                    if n_poly.is_point_inside_bound_rect(o_poly.center):
                        orig_obj = o_obj
                        break
                else:  # could not be matched with any original object
                    orig_obj = None
                if orig_obj is None:
                    new_ap = Aperture('{}_{}'.format(self.identifier, i), new_geo)
                    self.add_aperture(new_ap)
                elif isinstance(orig_obj, Aperture):
                    new_ap = orig_obj.duplicate()
                    new_ap._geometry = new_geo
                    self.add_aperture(new_ap)
                elif isinstance(orig_obj, Door):
                    new_door = orig_obj.duplicate()
                    new_door._geometry = new_geo
                    self.add_door(new_door)

    def project_and_add_sub_face(self, sub_face, angle_tolerance=None):
        """Project an Aperture or Door into this Face and add it to the Face.

        Args:
            sub_face: An Aperture or Door to be projected into this Face and added
                to it.
            angle_tolerance: An optional angle tolerance in degrees to be
                used to check whether the plane of the sub-face is parallel
                with this Face before merging.If None, no check will be
                performed. (Default: None).
        """
        parallel = True
        if angle_tolerance is not None:
            a_tol_min = math.radians(angle_tolerance)
            a_tol_max = math.pi - a_tol_min
            if a_tol_min < sub_face.normal.angle(self.normal) < a_tol_max:
                parallel = False
        if parallel:
            pl = self.geometry.plane
            geo = sub_face.geometry
            bound = [pl.project_point(pt) for pt in geo.boundary]
            holes = [[pl.project_point(pt) for pt in h] for h in geo.holes] \
                if geo.has_holes else None
            sub_face._geometry = Face3D(bound, pl, holes)
            self.add_sub_face(sub_face)

    def fix_invalid_sub_faces(
            self, trim_with_parent=True, union_overlaps=True,
            offset_distance=0.05, tolerance=0.01):
        """Fix invalid Apertures and Doors on this face by performing two operations.

        First, sub-faces that extend past their parent Face are trimmed with the
        parent and will have their edges offset towards the inside of the Face.
        Second, any sub-faces that overlap or touch one another will be unioned
        into a single Aperture or Door.

        Args:
            trim_with_parent: Boolean to note whether the fixing operation should
                check all sub-faces that extend past their parent and trim
                them, offsetting them towards the inside of the Face. (Default: True).
            union_overlaps: Boolean to note whether the fixing operation should
                check all sub-faces that overlap with one another and union any
                sub-faces together that overlap. (Default: True).
            offset_distance: Distance from the edge of the parent Face that the
                sub-faces will be offset to in order to make them valid. This
                should be larger than the tolerance. (Default: 0.05, suitable for
                    objects in meters).
            tolerance: The minimum difference between point values for them to be
                considered the distinct. (Default: 0.01, suitable for objects
                in meters).
        """
        # collect the sub-face geometries as polygons in the face plane
        clean_polys, original_objs, original_area = [], [], 0
        prim_pl = self.geometry.plane
        for sub_f in self.sub_faces:
            try:
                verts_2d = tuple(prim_pl.xyz_to_xy(pt) for pt in sub_f.geometry.boundary)
                poly = Polygon2D(verts_2d).remove_colinear_vertices(tolerance)
                clean_polys.append(poly)
                original_area += poly.area
                original_objs.append(sub_f)
            except AssertionError:  # degenerate geometry to ignore
                pass
        original_polys = clean_polys[:]

        # trim objects with the parent polygon if they extend past it
        if trim_with_parent:
            face_3d = self.geometry
            verts2d = tuple(prim_pl.xyz_to_xy(pt) for pt in face_3d.boundary)
            parent_poly, parent_holes = Polygon2D(verts2d), None
            if face_3d.has_holes:
                parent_holes = tuple(
                    Polygon2D(prim_pl.xyz_to_xy(pt) for pt in hole)
                    for hole in face_3d.holes
                )
            # loop through the polygons and offset them if they are not correctly bounded
            new_polygons = []
            for polygon in clean_polys:
                if not self._is_sub_polygon(polygon, parent_poly, parent_holes):
                    # find the boolean intersection of the polygon with the room
                    sub_face = Face3D([prim_pl.xy_to_xyz(pt) for pt in polygon])
                    bool_int = Face3D.coplanar_intersection(
                        face_3d, sub_face, tolerance, math.radians(1))
                    if bool_int is None:  # sub-face completely outside parent
                        continue
                    # offset the result of the boolean intersection from the edge
                    parent_edges = face_3d.boundary_segments if face_3d.holes is None \
                        else face_3d.boundary_segments + \
                        tuple(seg for hole in face_3d.hole_segments for seg in hole)
                    for new_f in bool_int:
                        new_pts_2d = []
                        for pt in new_f.boundary:
                            for edge in parent_edges:
                                close_pt = edge.closest_point(pt)
                                if pt.distance_to_point(close_pt) < offset_distance:
                                    move_vec = edge.v.rotate(prim_pl.n, math.pi / 2)
                                    move_vec = move_vec.normalize() * offset_distance
                                    pt = pt.move(move_vec)
                            new_pts_2d.append(prim_pl.xyz_to_xy(pt))
                        new_polygons.append(Polygon2D(new_pts_2d))
                else:
                    new_polygons.append(polygon)
            clean_polys = new_polygons

        # union overlaps and merge sub-faces that are touching
        if union_overlaps:
            grouped_polys = Polygon2D.group_by_overlap(clean_polys, tolerance)
            # union any of the polygons that overlap
            if not all(len(g) == 1 for g in grouped_polys):
                clean_polys = []
                for p_group in grouped_polys:
                    if len(p_group) == 1:
                        clean_polys.append(p_group[0])
                    else:
                        union_poly = Polygon2D.boolean_union_all(p_group, tolerance)
                        for new_poly in union_poly:
                            clean_polys.append(
                                new_poly.remove_colinear_vertices(tolerance))
            # join the polygons that touch one another
            clean_polys = Polygon2D.joined_intersected_boundary(clean_polys, tolerance)

        # assuming that the operations have edited the polygons, create new sub-faces
        new_area = sum(p.area for p in clean_polys)
        area_diff = abs(original_area - new_area)
        if len(clean_polys) != len(original_polys) or area_diff > tolerance:
            self.remove_sub_faces()
            for i, n_poly in enumerate(clean_polys):
                new_geo = Face3D([prim_pl.xy_to_xyz(pt) for pt in n_poly], prim_pl)
                for o_poly, o_obj in zip(original_polys, original_objs):
                    if n_poly.is_point_inside_bound_rect(o_poly.center):
                        orig_obj = o_obj
                        break
                else:  # could not be matched with any original object
                    orig_obj = None
                if orig_obj is None:
                    new_ap = Aperture('{}_{}'.format(self.identifier, i), new_geo)
                    self.add_aperture(new_ap)
                elif isinstance(orig_obj, Aperture):
                    new_ap = orig_obj.duplicate()
                    new_ap._geometry = new_geo
                    self.add_aperture(new_ap)
                elif isinstance(orig_obj, Door):
                    new_door = orig_obj.duplicate()
                    new_door._geometry = new_geo
                    self.add_door(new_door)

    def apertures_by_ratio(self, ratio, tolerance=0.01, rect_split=True):
        """Add apertures to this Face given a ratio of aperture area to face area.

        Note that this method removes any existing apertures and doors on the Face.
        This method attempts to generate as few apertures as necessary to meet the ratio.

        Args:
            ratio: A number between 0 and 1 (but not perfectly equal to 1)
                for the desired ratio between aperture area and face area.
            tolerance: The maximum difference between point values for them to be
                considered the same. This is used in the event that this face is
                concave and an attempt to subdivide the face into a rectangle is
                made. It does not affect the ability to produce apertures for
                convex Faces. Default: 0.01, suitable for objects in meters.
            rect_split: Boolean to note whether rectangular portions of base Face
                should be extracted before scaling them to create apertures. For
                gabled geometries, the resulting apertures will consist of one
                rectangle and one triangle, which can often look more realistic
                and is a better input for engines like EnergyPlus that cannot
                model windows with more than 4 vertices. However, if a single
                pentagonal window is desired for a gabled shape, this input can
                be set to False to produce such a result.

        Usage:

        .. code-block:: python

            room = Room.from_box(3.0, 6.0, 3.2, 180)
            room.faces[1].apertures_by_ratio(0.4)
        """
        assert 0 <= ratio < 1, 'Ratio must be between 0 and 1. Got {}'.format(ratio)
        self._acceptable_sub_face_check(Aperture)
        self.remove_sub_faces()
        if ratio == 0:
            return
        try:
            geo = self._geometry.remove_colinear_vertices(tolerance)
        except AssertionError:  # degenerate face that should not have apertures
            return
        if rect_split:
            ap_faces = geo.sub_faces_by_ratio_rectangle(ratio, tolerance)
        else:
            ap_faces = geo.sub_faces_by_ratio(ratio)
        for i, ap_face in enumerate(ap_faces):
            aperture = Aperture('{}_Glz{}'.format(self.identifier, i), ap_face)
            self.add_aperture(aperture)

    def apertures_by_ratio_rectangle(self, ratio, aperture_height, sill_height,
                                     horizontal_separation, vertical_separation=0,
                                     tolerance=0.01):
        """Add apertures to this face given a ratio of aperture area to face area.

        Note that this method removes any existing apertures on the Face.

        This function is virtually equivalent to the apertures_by_ratio method but
        any rectangular portions of this face will produce customizable rectangular
        apertures using the other inputs (aperture_height, sill_height,
        horizontal_separation, vertical_separation).

        Args:
            ratio: A number between 0 and 0.95 for the ratio between the area of
                the apertures and the area of this face.
            aperture_height: A number for the target height of the output apertures.
                Note that, if the ratio is too large for the height, the ratio will
                take precedence and the actual aperture_height will be larger
                than this value.
            sill_height: A number for the target height above the bottom edge of
                the rectangle to start the apertures. Note that, if the
                ratio is too large for the height, the ratio will take precedence
                and the sill_height will be smaller than this value.
            horizontal_separation: A number for the target separation between
                individual aperture center lines.  If this number is larger than
                the parent rectangle base, only one aperture will be produced.
            vertical_separation: An optional number to create a single vertical
                separation between top and bottom apertures. The default is
                0 for no separation.
            tolerance: The maximum difference between point values for them to be
                considered a part of a rectangle. Default: 0.01, suitable for
                objects in meters.

        Usage:

        .. code-block:: python

            room = Room.from_box(3.0, 6.0, 3.2, 180)
            room.faces[1].apertures_by_ratio_rectangle(0.4, 2, 0.9, 3)
        """
        assert 0 <= ratio <= 0.95, \
            'Ratio must be between 0 and 0.95. Got {}'.format(ratio)
        self._acceptable_sub_face_check(Aperture)
        self.remove_sub_faces()
        if ratio == 0:
            return
        try:
            geo = self._geometry.remove_colinear_vertices(tolerance)
        except AssertionError:  # degenerate face that should not have apertures
            return
        ap_faces = geo.sub_faces_by_ratio_sub_rectangle(
            ratio, aperture_height, sill_height, horizontal_separation,
            vertical_separation, tolerance)
        for i, ap_face in enumerate(ap_faces):
            aperture = Aperture('{}_Glz{}'.format(self.identifier, i), ap_face)
            self.add_aperture(aperture)

    def apertures_by_ratio_gridded(self, ratio, x_dim, y_dim=None, tolerance=0.01):
        """Add apertures to this face given a ratio of aperture area to face area.

        Note that this method removes any existing apertures on the Face.

        Apertures will be arranged in a grid derived from this face's plane.
        Because the x_dim and y_dim refer to dimensions within the X and Y
        coordinate system of this faces's plane, rotating this plane will
        result in rotated grid cells. This is particularly useful for generating
        skylights based on a glazing ratio.

        If the x_dim and/or y_dim are too large for this face, this method will
        return essentially the same result as the apertures_by_ratio method.

        Args:
            ratio: A number between 0 and 1 for the ratio between the area of
                the apertures and the area of this face.
            x_dim: The x dimension of the grid cells as a number.
            y_dim: The y dimension of the grid cells as a number. Default is None,
                which will assume the same cell dimension for y as is set for x.
            tolerance: The maximum difference between point values for them to be
                considered a part of a rectangle. Default: 0.01, suitable for
                objects in meters.

        Usage:

        .. code-block:: python

            room = Room.from_box(3.0, 6.0, 3.2, 180)
            room.faces[-1].apertures_by_ratio_gridded(0.05, 3)
        """
        assert 0 <= ratio < 1, 'Ratio must be between 0 and 1. Got {}'.format(ratio)
        self._acceptable_sub_face_check(Aperture)
        self.remove_sub_faces()
        if ratio == 0:
            return
        try:
            geo = self._geometry.remove_colinear_vertices(tolerance)
        except AssertionError:  # degenerate face that should not have apertures
            return
        ap_faces = geo.sub_faces_by_ratio_gridded(ratio, x_dim, y_dim)
        for i, ap_face in enumerate(ap_faces):
            aperture = Aperture('{}_Glz{}'.format(self.identifier, i), ap_face)
            self.add_aperture(aperture)

    def apertures_by_width_height_rectangle(self, aperture_height, aperture_width,
                                            sill_height, horizontal_separation,
                                            tolerance=0.01):
        """Add repeating apertures to this face given the aperture width and height.

        Note that this method removes any existing apertures on the Face.

        Note that this method will effectively fill any rectangular portions of
        this Face with apertures at the specified width, height and separation.
        If no rectangular portion of this Face can be identified, no apertures
        will be added.

        Args:
            aperture_height: A number for the target height of the apertures.
            aperture_width: A number for the target width of the apertures.
            sill_height: A number for the target height above the bottom edge of
                the rectangle to start the apertures. If the aperture_height
                is too large for the sill_height to fit within the rectangle,
                the aperture_height will take precedence.
            horizontal_separation: A number for the target separation between
                individual apertures center lines.  If this number is larger than
                the parent rectangle base, only one aperture will be produced.
            tolerance: The maximum difference between point values for them to be
                considered a part of a rectangle. Default: 0.01, suitable for
                objects in meters.

        Usage:

        .. code-block:: python

            room = Room.from_box(5.0, 10.0, 3.2, 180)
            room.faces[1].apertures_by_width_height_rectangle(1.5, 2, 0.8, 2.5)
        """
        assert horizontal_separation > 0, \
            'horizontal_separation must be above 0. Got {}'.format(horizontal_separation)
        if aperture_height <= 0 or aperture_width <= 0:
            return
        self._acceptable_sub_face_check(Aperture)
        self.remove_sub_faces()
        try:
            geo = self._geometry.remove_colinear_vertices(tolerance)
        except AssertionError:  # degenerate face that should not have apertures
            return
        ap_faces = geo.sub_faces_by_dimension_rectangle(
            aperture_height, aperture_width, sill_height, horizontal_separation,
            tolerance)
        for i, ap_face in enumerate(ap_faces):
            aperture = Aperture('{}_Glz{}'.format(self.identifier, i), ap_face)
            self.add_aperture(aperture)

    def aperture_by_width_height(self, width, height, sill_height=1,
                                 aperture_identifier=None):
        """Add a single rectangular aperture to the center of this Face.

        A rectangular window with the input width and height will always be added
        by this method regardless of whether this parent Face contains a recognizable
        rectangular portion or not. Furthermore, this method preserves any existing
        apertures on the Face.

        While the resulting aperture will always be in the plane of this Face,
        this method will not check to ensure that the aperture has all of its
        vertices completely within the boundary of this Face or that it does not
        intersect with other apertures in the Face. The are_sub_faces_valid()
        method can be used afterwards to check this.

        Args:
            width: A number for the Aperture width.
            height: A number for the Aperture height.
            sill_height: A number for the sill height. (Default: 1).
            aperture_identifier: Optional string for the aperture identifier.
                If None, the default will follow the convention
                "[face_identifier]_Glz[count]" where [count] is one more than
                the current number of apertures in the face.

        Returns:
            The new Aperture object that has been generated.

        Usage:

        .. code-block:: python

            room = Room.from_box(3.0, 6.0, 3.2, 180)
            room[1].aperture_by_width_height(2, 2, .7)  # aperture in front
            room[2].aperture_by_width_height(4, 1.5, .5)  # aperture on right
            room[2].aperture_by_width_height(4, 0.5, 2.2)  # aperture on right
        """
        # Perform checks
        if width <= 0 or height <= 0:
            return
        self._acceptable_sub_face_check(Aperture)
        # Generate the aperture geometry
        origin = self._geometry.lower_left_counter_clockwise_vertices[0]
        face_plane = Plane(self._geometry.plane.n, origin)
        if face_plane.y.z < 0:
            face_plane = face_plane.rotate(face_plane.n, math.pi, face_plane.o)
        center2d = face_plane.xyz_to_xy(self._geometry.center)
        x_dist = width / 2
        lower_left = Point2D(center2d.x - x_dist, sill_height)
        lower_right = Point2D(center2d.x + x_dist, sill_height)
        upper_right = Point2D(center2d.x + x_dist, sill_height + height)
        upper_left = Point2D(center2d.x - x_dist, sill_height + height)
        ap_verts2d = (lower_left, lower_right, upper_right, upper_left)
        ap_verts3d = tuple(face_plane.xy_to_xyz(pt) for pt in ap_verts2d)
        ap_face = Face3D(ap_verts3d, self._geometry.plane)
        if self.normal.angle(ap_face.normal) > math.pi / 2:  # reversed normal
            ap_face = ap_face.flip()

        # Create the aperture and add it to this Face
        identifier = aperture_identifier or \
            '{}_Glz{}'.format(self.identifier, len(self.apertures))
        aperture = Aperture(identifier, ap_face)
        self.add_aperture(aperture)
        return aperture

    def overhang(self, depth, angle=0, indoor=False, tolerance=0.01, base_name=None):
        """Add an overhang to this Face.

        Args:
            depth: A number for the overhang depth.
            angle: A number for the for an angle to rotate the overhang in degrees.
                Positive numbers indicate a downward rotation while negative numbers
                indicate an upward rotation. Default is 0 for no rotation.
            indoor: Boolean for whether the overhang should be generated facing the
                opposite direction of the aperture normal (typically meaning
                indoor geometry). Default: False.
            tolerance: An optional value to not add the overhang if it has a length less
                than the tolerance. Default: 0.01, suitable for objects in meters.
            base_name: Optional base identifier for the shade objects. If None,
                the default is InOverhang or OutOverhang depending on whether
                indoor is True.

        Returns:
            A list of the new Shade objects that have been generated.
        """
        if base_name is None:
            base_name = 'InOverhang' if indoor else 'OutOverhang'
        return self.louvers_by_count(1, depth, angle=angle, indoor=indoor,
                                     tolerance=tolerance, base_name=base_name)

    def louvers(self, depth, louver_count=None, distance=None, offset=0, angle=0,
                contour_vector=Vector2D(0, 1), flip_start_side=False,
                indoor=False, tolerance=0.01, base_name=None):
        """Add a series of louvered Shade objects over this Face.

        If both louver_count and distance are None, this method will add a
        single louver shade following the other criteria.

        Args:
            depth: A number for the depth to extrude the louvers.
            louver_count: A positive integer for the number of louvers to generate.
                If None, louvers will be generated to fill the Face at the
                specified distance. (Default: None).
            distance: A number for the approximate distance between each louver.
                If None, louvers will be generated to fill the Face at the
                specified louver_count. (Default: None).
            offset: A number for the distance to louvers from this Face.
                Default is 0 for no offset.
            angle: A number for the for an angle to rotate the louvers in degrees.
                Positive numbers indicate a downward rotation while negative numbers
                indicate an upward rotation. Default is 0 for no rotation.
            contour_vector: A Vector2D for the direction along which contours
                are generated. This 2D vector will be interpreted into a 3D vector
                within the plane of this Face. (0, 1) will usually generate
                horizontal contours in 3D space, (1, 0) will generate vertical
                contours, and (1, 1) will generate diagonal contours. Default: (0, 1).
            flip_start_side: Boolean to note whether the side the louvers start from
                should be flipped. Default is False to have louvers on top or right.
                Setting to True will start contours on the bottom or left.
            indoor: Boolean for whether louvers should be generated facing the
                opposite direction of the Face normal (typically meaning
                indoor geometry). Default: False.
            tolerance: An optional value to remove any louvers with a length less
                than the tolerance. Default: 0.01, suitable for objects in meters.
            base_name: Optional base identifier for the shade objects. If None,
                the default is InShd or OutShd depending on whether indoor is True.

        Returns:
            A list of the new Shade objects that have been generated.
        """
        if depth == 0 or louver_count == 0:
            return []
        elif louver_count is None and distance is None:
            return self.louvers_by_count(
                1, depth, offset, angle, contour_vector, flip_start_side, indoor,
                tolerance=tolerance, base_name=base_name)
        elif distance is None:
            return self.louvers_by_count(
                louver_count, depth, offset, angle, contour_vector,
                flip_start_side, indoor, tolerance=tolerance, base_name=base_name)
        else:
            return self.louvers_by_distance_between(
                distance, depth, offset, angle, contour_vector, flip_start_side, indoor,
                tolerance=tolerance, max_count=louver_count, base_name=base_name)

    def louvers_by_count(self, louver_count, depth, offset=0, angle=0,
                         contour_vector=Vector2D(0, 1), flip_start_side=False,
                         indoor=False, tolerance=0.01, base_name=None):
        """Add louvered Shade objects over this Face to hit a target louver_count.

        Args:
            louver_count: A positive integer for the number of louvers to generate.
            depth: A number for the depth to extrude the louvers.
            offset: A number for the distance to louvers from this Face.
                Default is 0 for no offset.
            angle: A number for the for an angle to rotate the louvers in degrees.
                Positive numbers indicate a downward rotation while negative numbers
                indicate an upward rotation. Default is 0 for no rotation.
            contour_vector: A Vector2D for the direction along which contours
                are generated. This 2D vector will be interpreted into a 3D vector
                within the plane of this Face. (0, 1) will usually generate
                horizontal contours in 3D space, (1, 0) will generate vertical
                contours, and (1, 1) will generate diagonal contours. Default: (0, 1).
            flip_start_side: Boolean to note whether the side the louvers start from
                should be flipped. Default is False to have louvers on top or right.
                Setting to True will start contours on the bottom or left.
            indoor: Boolean for whether louvers should be generated facing the
                opposite direction of the Face normal (typically meaning
                indoor geometry). Default: False.
            tolerance: An optional value to remove any louvers with a length less
                than the tolerance. Default: 0.01, suitable for objects in meters.
            base_name: Optional base identifier for the shade objects. If None,
                the default is InShd or OutShd depending on whether indoor is True.

        Returns:
            A list of the new Shade objects that have been generated.
        """
        assert louver_count > 0, 'louver_count must be greater than 0.'
        angle = math.radians(angle)
        louvers = []
        face_geo = self.geometry if indoor is False else self.geometry.flip()
        if base_name is None:
            shd_name_base = '{}_InShd{}' if indoor else '{}_OutShd{}'
        else:
            shd_name_base = '{}_' + str(base_name) + '{}'
        shade_faces = face_geo.contour_fins_by_number(
            louver_count, depth, offset, angle,
            contour_vector, flip_start_side, tolerance)
        for i, shade_geo in enumerate(shade_faces):
            louvers.append(Shade(shd_name_base.format(self.identifier, i), shade_geo))
        if indoor:
            self.add_indoor_shades(louvers)
        else:
            self.add_outdoor_shades(louvers)
        return louvers

    def louvers_by_distance_between(
            self, distance, depth, offset=0, angle=0, contour_vector=Vector2D(0, 1),
            flip_start_side=False, indoor=False, tolerance=0.01, max_count=None,
            base_name=None):
        """Add louvered Shade objects over this Face to hit a target distance between.

        Args:
            distance: A number for the approximate distance between each louver.
            depth: A number for the depth to extrude the louvers.
            offset: A number for the distance to louvers from this Face.
                Default is 0 for no offset.
            angle: A number for the for an angle to rotate the louvers in degrees.
                Positive numbers indicate a downward rotation while negative numbers
                indicate an upward rotation. Default is 0 for no rotation.
            contour_vector: A Vector2D for the direction along which contours
                are generated. This 2D vector will be interpreted into a 3D vector
                within the plane of this Face. (0, 1) will usually generate
                horizontal contours in 3D space, (1, 0) will generate vertical
                contours, and (1, 1) will generate diagonal contours. Default: (0, 1).
            flip_start_side: Boolean to note whether the side the louvers start from
                should be flipped. Default is False to have contours on top or right.
                Setting to True will start contours on the bottom or left.
            indoor: Boolean for whether louvers should be generated facing the
                opposite direction of the Face normal (typically meaning
                indoor geometry). Default: False.
            tolerance: An optional value to remove any louvers with a length less
                than the tolerance. Default: 0.01, suitable for objects in meters.
            max_count: Optional integer to set the maximum number of louvers that
                will be generated. If None, louvers will cover the entire face.
            base_name: Optional base identifier for the shade objects. If None, the
                default is InShd or OutShd depending on whether indoor is True.

        Returns:
            A list of the new Shade objects that have been generated.
        """
        # set defaults
        angle = math.radians(angle)
        face_geo = self.geometry if indoor is False else self.geometry.flip()
        if base_name is None:
            shd_name_base = '{}_InShd{}' if indoor else '{}_OutShd{}'
        else:
            shd_name_base = '{}_' + str(base_name) + '{}'

        # generate shade geometries
        shade_faces = face_geo.contour_fins_by_distance_between(
            distance, depth, offset, angle, contour_vector, flip_start_side, tolerance)
        if max_count:
            try:
                shade_faces = shade_faces[:max_count]
            except IndexError:  # fewer shades were generated than the max count
                pass

        # create the shade objects
        louvers = []
        for i, shade_geo in enumerate(shade_faces):
            louvers.append(Shade(shd_name_base.format(self.identifier, i), shade_geo))
        if indoor:
            self.add_indoor_shades(louvers)
        else:
            self.add_outdoor_shades(louvers)
        return louvers

    def move(self, moving_vec):
        """Move this Face along a vector.

        Args:
            moving_vec: A ladybug_geometry Vector3D with the direction and distance
                to move the face.
        """
        self._geometry = self.geometry.move(moving_vec)
        for ap in self._apertures:
            ap.move(moving_vec)
        for dr in self._doors:
            dr.move(moving_vec)
        self.move_shades(moving_vec)
        self.properties.move(moving_vec)
        self._punched_geometry = None  # reset so that it can be re-computed

    def rotate(self, axis, angle, origin):
        """Rotate this Face by a certain angle around an axis and origin.

        Args:
            axis: A ladybug_geometry Vector3D axis representing the axis of rotation.
            angle: An angle for rotation in degrees.
            origin: A ladybug_geometry Point3D for the origin around which the
                object will be rotated.
        """
        self._geometry = self.geometry.rotate(axis, math.radians(angle), origin)
        for ap in self._apertures:
            ap.rotate(axis, angle, origin)
        for dr in self._doors:
            dr.rotate(axis, angle, origin)
        self.rotate_shades(axis, angle, origin)
        self.properties.rotate(axis, angle, origin)
        self._punched_geometry = None  # reset so that it can be re-computed

    def rotate_xy(self, angle, origin):
        """Rotate this Face counterclockwise in the world XY plane by a certain angle.

        Args:
            angle: An angle in degrees.
            origin: A ladybug_geometry Point3D for the origin around which the
                object will be rotated.
        """
        self._geometry = self.geometry.rotate_xy(math.radians(angle), origin)
        for ap in self._apertures:
            ap.rotate_xy(angle, origin)
        for dr in self._doors:
            dr.rotate_xy(angle, origin)
        self.rotate_xy_shades(angle, origin)
        self.properties.rotate_xy(angle, origin)
        self._punched_geometry = None  # reset so that it can be re-computed

    def reflect(self, plane):
        """Reflect this Face across a plane.

        Args:
            plane: A ladybug_geometry Plane across which the object will
                be reflected.
        """
        self._geometry = self.geometry.reflect(plane.n, plane.o)
        for ap in self._apertures:
            ap.reflect(plane)
        for dr in self._doors:
            dr.reflect(plane)
        self.reflect_shades(plane)
        self.properties.reflect(plane)
        self._punched_geometry = None  # reset so that it can be re-computed

    def scale(self, factor, origin=None):
        """Scale this Face by a factor from an origin point.

        Args:
            factor: A number representing how much the object should be scaled.
            origin: A ladybug_geometry Point3D representing the origin from which
                to scale. If None, it will be scaled from the World origin (0, 0, 0).
        """
        self._geometry = self.geometry.scale(factor, origin)
        for ap in self._apertures:
            ap.scale(factor, origin)
        for dr in self._doors:
            dr.scale(factor, origin)
        self.scale_shades(factor, origin)
        self.properties.scale(factor, origin)
        self._punched_geometry = None  # reset so that it can be re-computed

    def remove_colinear_vertices(self, tolerance=0.01):
        """Remove all colinear and duplicate vertices from this object's geometry.

        Note that this does not affect any assigned Apertures, Doors or Shades.

        Args:
            tolerance: The minimum distance between a vertex and the boundary segments
                at which point the vertex is considered colinear. Default: 0.01,
                suitable for objects in meters.
        """
        try:
            self._geometry = self.geometry.remove_colinear_vertices(tolerance)
        except AssertionError as e:  # usually a sliver face of some kind
            raise ValueError(
                'Face "{}" is invalid with dimensions less than the '
                'tolerance.\n{}'.format(self.full_id, e))
        self._punched_geometry = None  # reset so that it can be re-computed

    def remove_degenerate_sub_faces(self, tolerance=0.01):
        """Remove colinear vertices from sub-faces and eliminate degenerate ones.

        Args:
            tolerance: The minimum distance between a vertex and the boundary segments
                at which point the vertex is considered colinear. Default: 0.01,
                suitable for objects in meters.
        """
        # set up lists to track sub-faces to remove
        del_ap_i, del_dr_i = [], []
        # remove degenerate apertures
        for i, ap in enumerate(self._apertures):
            try:
                ap.remove_colinear_vertices(tolerance)
            except ValueError:
                del_ap_i.append(i)
        for del_i in reversed(del_ap_i):
            self._apertures.pop(del_i)
        # remove degenerate doors
        for i, dr in enumerate(self._doors):
            try:
                dr.remove_colinear_vertices(tolerance)
            except ValueError:
                del_dr_i.append(i)
        for del_i in reversed(del_dr_i):
            self._doors.pop(del_i)

    def is_geo_equivalent(self, face, tolerance=0.01):
        """Get a boolean for whether this object is geometrically equivalent to another.

        This will also check all child Apertures and Doors for equivalency but not
        assigned shades.

        Args:
            face: Another Face for which geometric equivalency will be tested.
            tolerance: The minimum difference between the coordinate values of two
                vertices at which they can be considered geometrically equivalent.

        Returns:
            True if geometrically equivalent. False if not geometrically equivalent.
        """
        meta_1 = (self.display_name, self.type, self.boundary_condition)
        meta_2 = (face.display_name, face.type, face.boundary_condition)
        if meta_1 != meta_2:
            return False
        if abs(self.area - face.area) > tolerance * self.area:
            return False
        if not self.geometry.is_centered_adjacent(face.geometry, tolerance):
            return False
        if len(self._apertures) != len(face._apertures):
            return False
        if len(self._doors) != len(face._doors):
            return False
        for ap1, ap2 in zip(self._apertures, face._apertures):
            if not ap1.is_geo_equivalent(ap2, tolerance):
                return False
        for dr1, dr2 in zip(self._doors, face._doors):
            if not dr1.is_geo_equivalent(dr2, tolerance):
                return False
        if not self._are_shades_equivalent(face, tolerance):
            return False
        return True

    def check_sub_faces_valid(self, tolerance=0.01, angle_tolerance=1,
                              raise_exception=True, detailed=False):
        """Check that sub-faces are co-planar with this Face within the Face boundary.

        Note this does not check the planarity of the sub-faces themselves, whether
        they self-intersect, or whether they have a non-zero area.

        Args:
            tolerance: The minimum difference between the coordinate values of two
                vertices at which they can be considered equivalent. Default: 0.01,
                suitable for objects in meters.
            angle_tolerance: The max angle in degrees that the plane normals can
                differ from one another in order for them to be considered coplanar.
                Default: 1 degree.
            raise_exception: Boolean to note whether a ValueError should be raised
                if an sub-face is not valid.
            detailed: Boolean for whether the returned object is a detailed list of
                dicts with error info or a string with a message. (Default: False).

        Returns:
            A string with the message or a list with dictionaries if detailed is True.
        """
        detailed = False if raise_exception else detailed
        ap = self.check_apertures_valid(tolerance, angle_tolerance, False, detailed)
        dr = self.check_doors_valid(tolerance, angle_tolerance, False, detailed)
        full_msgs = ap + dr if detailed else [m for m in (ap, dr) if m != '']
        if raise_exception and len(full_msgs) != 0:
            raise ValueError('\n'.join(full_msgs))
        return full_msgs if detailed else '\n'.join(full_msgs)

    def check_apertures_valid(self, tolerance=0.01, angle_tolerance=1,
                              raise_exception=True, detailed=False):
        """Check that apertures are co-planar with this Face within the Face boundary.

        Note this does not check the planarity of the apertures themselves, whether
        they self-intersect, or whether they have a non-zero area.

        Args:
            tolerance: The minimum difference between the coordinate values of two
                vertices at which they can be considered equivalent. Default: 0.01,
                suitable for objects in meters.
            angle_tolerance: The max angle in degrees that the plane normals can
                differ from one another in order for them to be considered coplanar.
                Default: 1 degree.
            raise_exception: Boolean to note whether a ValueError should be raised
                if an aperture is not valid.
            detailed: Boolean for whether the returned object is a detailed list of
                dicts with error info or a string with a message. (Default: False).

        Returns:
            A string with the message or a list with dictionaries if detailed is True.
        """
        detailed = False if raise_exception else detailed
        angle_tolerance = math.radians(angle_tolerance)
        msgs = []
        for ap in self._apertures:
            if not self.geometry.is_sub_face(ap.geometry, tolerance, angle_tolerance):
                msg = 'Aperture "{}" is not coplanar or fully bounded by its parent ' \
                    'Face "{}".'.format(ap.full_id, self.full_id)
                msg = self._validation_message_child(
                    msg, ap, detailed, '000104', error_type='Invalid Sub-Face Geometry')
                msgs.append(msg)
        full_msg = msgs if detailed else '\n'.join(msgs)
        if raise_exception and len(msgs) != 0:
            raise ValueError(full_msg)
        return full_msg

    def check_doors_valid(self, tolerance=0.01, angle_tolerance=1,
                          raise_exception=True, detailed=False):
        """Check that doors are co-planar with this Face within the Face boundary.

        Note this does not check the planarity of the doors themselves, whether
        they self-intersect, or whether they have a non-zero area.

        Args:
            tolerance: The minimum difference between the coordinate values of two
                vertices at which they can be considered equivalent. Default: 0.01,
                suitable for objects in meters.
            angle_tolerance: The max angle in degrees that the plane normals can
                differ from one another in order for them to be considered coplanar.
                Default: 1 degree.
            raise_exception: Boolean to note whether a ValueError should be raised
                if an door is not valid.
            detailed: Boolean for whether the returned object is a detailed list of
                dicts with error info or a string with a message. (Default: False).

        Returns:
            A string with the message or a list with dictionaries if detailed is True.
        """
        detailed = False if raise_exception else detailed
        angle_tolerance = math.radians(angle_tolerance)
        msgs = []
        for dr in self._doors:
            if not self.geometry.is_sub_face(dr.geometry, tolerance, angle_tolerance):
                msg = 'Door "{}" is not coplanar or fully bounded by its parent ' \
                    'Face "{}".'.format(dr.full_id, self.full_id)
                msg = self._validation_message_child(
                    msg, dr, detailed, '000104', error_type='Invalid Sub-Face Geometry')
                msgs.append(msg)
        full_msg = msgs if detailed else '\n'.join(msgs)
        if raise_exception and len(msgs) != 0:
            raise ValueError(full_msg)
        return full_msg

    def check_sub_faces_overlapping(
            self, tolerance=0.01, raise_exception=True, detailed=False):
        """Check that this Face's sub-faces do not overlap with one another.

        Args:
            tolerance: The minimum distance that two sub-faces must overlap in order
                for them to be considered overlapping and invalid. (Default: 0.01,
                suitable for objects in meters).
            raise_exception: Boolean to note whether a ValueError should be raised
                if a sub-faces overlap with one another.
            detailed: Boolean for whether the returned object is a detailed list of
                dicts with error info or a string with a message. (Default: False).

        Returns:
            A string with the message or a list with dictionaries if detailed is True.
        """
        sub_faces = self.sub_faces
        if len(sub_faces) == 0:
            return [] if detailed else ''
        sf_groups = self._group_sub_faces_by_overlap(sub_faces, tolerance)
        if not all(len(g) == 1 for g in sf_groups):
            base_msg = 'Face "{}" contains Apertures and/or ' \
                'Doors that overlap with each other.'.format(self.full_id)
            if raise_exception:
                raise ValueError(base_msg)
            if not detailed:  # just give a message about the Face if not detailed
                return base_msg
            all_overlaps = []
            for sf_group in sf_groups:
                if len(sf_group) != 1:
                    det_msg = 'The following sub-faces overlap with one another:' \
                        '\n{}'.format('\n'.join([sf.full_id for sf in sf_group]))
                    msg = '{}\n{}'.format(base_msg, det_msg)
                    err_obj = self._validation_message_child(
                        msg, sf_group[0], detailed, '000105',
                        error_type='Overlapping Sub-Face Geometry')
                    err_obj['element_type'] = 'SubFace'
                    for ov_obj in sf_group[1:]:
                        err_obj['element_id'].append(ov_obj.identifier)
                        err_obj['element_name'].append(ov_obj.display_name)
                        err_obj['parents'].append(err_obj['parents'][0])
                    all_overlaps.append(err_obj)
            return all_overlaps
        return [] if detailed else ''

    def check_upside_down(self, angle_tolerance=1, raise_exception=True, detailed=False):
        """Check whether the face is pointing in the correct direction for the face type.

        This method will only report Floors that are pointing upwards or RoofCeilings
        that are pointed downwards. These cases are likely modeling errors and are in
        danger of having their vertices flipped by EnergyPlus, causing them to
        not see the sun.

        Args:
            angle_tolerance: The max angle in degrees that the Face normal can
                differ from up or down before it is considered a case of a downward
                pointing RoofCeiling or upward pointing Floor. Default: 1 degree.
            raise_exception: Boolean to note whether an ValueError should be
                raised if the Face is an an upward pointing Floor or a downward
                pointing RoofCeiling.
            detailed: Boolean for whether the returned object is a detailed list of
                dicts with error info or a string with a message. (Default: False).

        Returns:
            A string with the message or a list with a dictionary if detailed is True.
        """
        msg = None
        if isinstance(self.type, Floor) and self.altitude > 90 - angle_tolerance:
            msg = 'Face "{}" is an upward-pointing Floor, which should be ' \
                'changed to a RoofCeiling.'.format(self.full_id)
        elif isinstance(self.type, RoofCeiling) and self.altitude < angle_tolerance - 90:
            msg = 'Face "{}" is an downward-pointing RoofCeiling, which should be ' \
                'changed to a Floor.'.format(self.full_id)
        if msg:
            full_msg = self._validation_message(
                msg, raise_exception, detailed, '000109',
                error_type='Upside Down Face')
            return full_msg
        return [] if detailed else ''

    def check_planar(self, tolerance=0.01, raise_exception=True, detailed=False):
        """Check whether all of the Face's vertices lie within the same plane.

        Args:
            tolerance: The minimum distance between a given vertex and a the
                object's plane at which the vertex is said to lie in the plane.
                Default: 0.01, suitable for objects in meters.
            raise_exception: Boolean to note whether an ValueError should be
                raised if a vertex does not lie within the object's plane.
            detailed: Boolean for whether the returned object is a detailed list of
                dicts with error info or a string with a message. (Default: False).

        Returns:
            A string with the message or a list with a dictionary if detailed is True.
        """
        try:
            self.geometry.check_planar(tolerance, raise_exception=True)
        except ValueError as e:
            msg = 'Face "{}" is not planar.\n{}'.format(self.full_id, e)
            full_msg = self._validation_message(
                msg, raise_exception, detailed, '000101',
                error_type='Non-Planar Geometry')
            if detailed:  # add the out-of-plane points to helper_geometry
                help_pts = [
                    p.to_dict() for p in self.geometry.non_planar_vertices(tolerance)
                ]
                full_msg[0]['helper_geometry'] = help_pts
            return full_msg
        return [] if detailed else ''

    def check_self_intersecting(self, tolerance=0.01, raise_exception=True,
                                detailed=False):
        """Check whether the edges of the Face intersect one another (like a bowtie).

        Note that objects that have duplicate vertices will not be considered
        self-intersecting and are valid in honeybee.

        Args:
            tolerance: The minimum difference between the coordinate values of two
                vertices at which they can be considered equivalent. Default: 0.01,
                suitable for objects in meters.
            raise_exception: If True, a ValueError will be raised if the object
                intersects with itself. Default: True.
            detailed: Boolean for whether the returned object is a detailed list of
                dicts with error info or a string with a message. (Default: False).

        Returns:
            A string with the message or a list with a dictionary if detailed is True.
        """
        if self.geometry.is_self_intersecting:
            msg = 'Face "{}" has self-intersecting edges.'.format(self.full_id)
            try:  # see if it is self-intersecting because of a duplicate vertex
                new_geo = self.geometry.remove_duplicate_vertices(tolerance)
                if not new_geo.is_self_intersecting:
                    return [] if detailed else ''  # valid with removed dup vertex
            except AssertionError:
                pass  # degenerate face; treat it as self-intersecting
            full_msg = self._validation_message(
                msg, raise_exception, detailed, '000102',
                error_type='Self-Intersecting Geometry')
            if detailed:  # add the self-intersection points to helper_geometry
                help_pts = [p.to_dict() for p in self.geometry.self_intersection_points]
                full_msg[0]['helper_geometry'] = help_pts
            return full_msg
        return [] if detailed else ''

    def check_degenerate(self, tolerance=0.01, raise_exception=True, detailed=False):
        """Check whether the Face is degenerate with effectively zero area.

        Note that, while the Face may have an area larger than the tolerance,
        removing colinear vertices within the tolerance would create a geometry
        smaller than the tolerance.

        Args:
            tolerance: The minimum difference between the coordinate values of two
                vertices at which they can be considered equivalent. Default: 0.01,
                suitable for objects in meters.
            raise_exception: If True, a ValueError will be raised if the object
                intersects with itself. Default: True.
            detailed: Boolean for whether the returned object is a detailed list of
                dicts with error info or a string with a message. (Default: False).

        Returns:
            A string with the message or a list with a dictionary if detailed is True.
        """
        msg = 'Face "{}" is degenerate and should be deleted.'.format(self.full_id)
        try:  # see if it is self-intersecting because of a duplicate vertex
            new_geo = self.geometry.remove_colinear_vertices(tolerance)
            if new_geo.area > tolerance:
                return [] if detailed else ''  # valid
        except AssertionError:
            pass  # degenerate face; treat it as degenerate
        full_msg = self._validation_message(
            msg, raise_exception, detailed, '000103',
            error_type='Zero-Area Geometry')
        return full_msg
        return [] if detailed else ''

    def display_dict(self):
        """Get a list of DisplayFace3D dictionaries for visualizing the object."""
        base = [self._display_face(self.punched_geometry, self.type_color)]
        for ap in self._apertures:
            base.extend(ap.display_dict())
        for dr in self._doors:
            base.extend(dr.display_dict())
        for shd in self.shades:
            base.extend(shd.display_dict())
        return base

    @property
    def to(self):
        """Face writer object.

        Use this method to access Writer class to write the face in other formats.

        Usage:

        .. code-block:: python

            face.to.idf(face) -> idf string.
            face.to.radiance(face) -> Radiance string.
        """
        return writer

    def to_dict(self, abridged=False, included_prop=None, include_plane=True):
        """Return Face as a dictionary.

        Args:
            abridged: Boolean to note whether the extension properties of the
                object (ie. materials, constructions) should be included in detail
                (False) or just referenced by identifier (True). (Default: False).
            included_prop: List of properties to filter keys that must be included in
                output dictionary. For example ['energy'] will include 'energy' key if
                available in properties to_dict. By default all the keys will be
                included. To exclude all the keys from extensions use an empty list.
            include_plane: Boolean to note wether the plane of the Face3D should be
                included in the output. This can preserve the orientation of the
                X/Y axes of the plane but is not required and can be removed to
                keep the dictionary smaller. (Default: True).
        """
        base = {'type': 'Face'}
        base['identifier'] = self.identifier
        base['display_name'] = self.display_name
        base['properties'] = self.properties.to_dict(abridged, included_prop)
        enforce_upper_left = True if 'energy' in base['properties'] else False
        base['geometry'] = self._geometry.to_dict(include_plane, enforce_upper_left)

        base['face_type'] = self.type.name
        if isinstance(self.boundary_condition, Outdoors) and \
                'energy' in base['properties']:
            base['boundary_condition'] = self.boundary_condition.to_dict(full=True)
        else:
            base['boundary_condition'] = self.boundary_condition.to_dict()

        if self._apertures != []:
            base['apertures'] = [ap.to_dict(abridged, included_prop, include_plane)
                                 for ap in self._apertures]
        if self._doors != []:
            base['doors'] = [dr.to_dict(abridged, included_prop, include_plane)
                             for dr in self._doors]
        self._add_shades_to_dict(base, abridged, included_prop, include_plane)
        if self.user_data is not None:
            base['user_data'] = self.user_data
        return base

    def _acceptable_sub_face_check(self, sub_face_type=Aperture):
        """Check whether the Face can accept sub-faces and raise an exception if not."""
        assert isinstance(self.boundary_condition, Outdoors), \
            '{} cannot be added to Face "{}" with a {} boundary condition.'.format(
                sub_face_type.__name__, self.full_id, self.boundary_condition)
        assert not isinstance(self.type, AirBoundary), \
            '{} cannot be added to AirBoundary Face "{}".'.format(
                sub_face_type.__name__, self.full_id)

    @staticmethod
    def _remove_overlapping_sub_faces(sub_faces, tolerance):
        """Get a list of Apertures and/or Doors with no overlaps.

        Args:
            sub_faces: A list of Apertures or Doors to be checked for overlapping.
            tolerance: The minimum distance from the edge of a neighboring Face3D
                at which a point is considered to overlap with that Face3D.

        Returns:
            A list of the input sub_faces with smaller overlapping geometries removed.
        """
        # group the sub-faces according to the overlaps with one another
        grouped_sfs = Face._group_sub_faces_by_overlap(sub_faces, tolerance)
        # build a list of sub-faces without any overlaps
        clean_sub_faces = []
        for sf_group in grouped_sfs:
            if len(sf_group) == 1:
                clean_sub_faces.append(sf_group[0])
            else:  # take the subface with the largest area
                sf_group.sort(key=lambda x: x.area, reverse=True)
                clean_sub_faces.append(sf_group[0])
        return clean_sub_faces

    @staticmethod
    def _group_sub_faces_by_overlap(sub_faces, tolerance):
        """Group a Apertures and/or Doors depending on whether they overlap one another.

        Args:
            sub_faces: A list of Apertures or Doors to be checked for overlapping.
            tolerance: The minimum distance from the edge of a neighboring Face3D
                at which a point is considered to overlap with that Face3D.

        Returns:
            A list of lists where each sub-list represents a group of Apertures and/or
            Doors that overlap with one another.
        """
        # sort the sub-faces by area
        sub_faces = list(sorted(sub_faces, key=lambda x: x.area, reverse=True))
        # create polygons for all of the faces
        r_plane = sub_faces[0].geometry.plane
        polygons = [Polygon2D([r_plane.xyz_to_xy(pt) for pt in face.vertices])
                    for face in sub_faces]
        # loop through the polygons and check to see if it overlaps with the others
        grouped_polys, grouped_sfs = [[polygons[0]]], [[sub_faces[0]]]
        for poly, face in zip(polygons[1:], sub_faces[1:]):
            group_found = False
            for poly_group, face_group in zip(grouped_polys, grouped_sfs):
                for oth_poly in poly_group:
                    if poly.polygon_relationship(oth_poly, tolerance) >= 0:
                        poly_group.append(poly)
                        face_group.append(face)
                        group_found = True
                        break
                if group_found:
                    break
            if not group_found:  # the polygon does not overlap with any of the others
                grouped_polys.append([poly])  # make a new group for the polygon
                grouped_sfs.append([face])  # make a new group for the face
        return grouped_sfs

    @staticmethod
    def _is_sub_polygon(sub_poly, parent_poly, parent_holes=None):
        """Check if a sub-polygon is valid for a given assumed parent polygon.

        Args:
            sub_poly: A sub-Polygon2D for which sub-face equivalency will be tested.
            parent_poly: A parent Polygon2D.
            parent_holes: An optional list of Polygon2D for any holes that may
                exist in the parent polygon. (Default: None).
        """
        if parent_holes is None:
            return parent_poly.is_polygon_inside(sub_poly)
        else:
            if not parent_poly.is_polygon_inside(sub_poly):
                return False
            for hole_poly in parent_holes:
                if not hole_poly.is_polygon_outside(sub_poly):
                    return False
            return True

    def __copy__(self):
        new_f = Face(self.identifier, self.geometry, self.type, self.boundary_condition)
        new_f._display_name = self._display_name
        new_f._user_data = None if self.user_data is None else self.user_data.copy()
        new_f._apertures = [ap.duplicate() for ap in self._apertures]
        new_f._doors = [dr.duplicate() for dr in self._doors]
        for ap in new_f._apertures:
            ap._parent = new_f
        for dr in new_f._doors:
            dr._parent = new_f
        self._duplicate_child_shades(new_f)
        new_f._punched_geometry = self._punched_geometry
        new_f._properties._duplicate_extension_attr(self._properties)
        return new_f

    def __repr__(self):
        return 'Face: %s' % self.display_name
